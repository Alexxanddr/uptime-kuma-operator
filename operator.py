import os
import kopf
import logging
import sys
import threading
import time
from uptime_kuma_api import UptimeKumaApi, MonitorType

# Configuration
KUMA_URL = os.getenv("KUMA_URL")
KUMA_USER = os.getenv("KUMA_USER")
KUMA_PASS = os.getenv("KUMA_PASS")
ANNOTATION_PREFIX = "uptime-kuma.io"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Configure root logger
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)

# Silence noisy secondary loggers
logging.getLogger('kopf').setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logging.getLogger('engineio').setLevel(logging.WARNING)
logging.getLogger('socketio').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

class KumaManager:
    """Manages a persistent connection to Uptime Kuma with thread-safe access."""
    def __init__(self):
        self.api = None
        self.lock = threading.Lock()
        self._last_use = 0

    def _connect(self):
        try:
            if self.api:
                try:
                    self.api.disconnect()
                except:
                    pass
            
            logging.info(f"Connecting to Uptime Kuma at {KUMA_URL}...")
            self.api = UptimeKumaApi(KUMA_URL)
            self.api.login(KUMA_USER, KUMA_PASS)
            self._last_use = time.time()
            logging.info("Connected and logged in successfully.")
        except Exception as e:
            logging.error(f"Failed to connect to Uptime Kuma: {e}")
            self.api = None
            raise

    def get_api(self):
        with self.lock:
            # Reconnect if never connected or if inactive for too long (e.g. 5 mins)
            # or if the socket says it's not connected (if we can detect it)
            if not self.api:
                self._connect()
            
            # Simple check if connection is still alive by doing a lightweight call
            try:
                # If it's been more than 60 seconds, check if alive
                if time.time() - self._last_use > 60:
                    self.api.get_monitors()
            except Exception:
                logging.warning("Connection lost, reconnecting...")
                self._connect()
            
            self._last_use = time.time()
            return self.api

kuma_manager = KumaManager()

def get_monitor_name(name, namespace):
    return f"k8s-{namespace}-{name}"

def parse_annotations(annotations):
    if not annotations:
        return None
    
    enabled_val = str(annotations.get(f"{ANNOTATION_PREFIX}/enabled", "false")).lower()
    if enabled_val != "true":
        return None
    
    try:
        config = {
            "type": annotations.get(f"{ANNOTATION_PREFIX}/type", "http").lower(),
            "url": annotations.get(f"{ANNOTATION_PREFIX}/url"),
            "hostname": annotations.get(f"{ANNOTATION_PREFIX}/hostname"),
            "port": int(annotations.get(f"{ANNOTATION_PREFIX}/port", 80)) if annotations.get(f"{ANNOTATION_PREFIX}/port") else None,
            "interval": int(annotations.get(f"{ANNOTATION_PREFIX}/interval", 60)),
            "maxretries": int(annotations.get(f"{ANNOTATION_PREFIX}/retries", 3)),
            "notifications": [n.strip() for n in annotations.get(f"{ANNOTATION_PREFIX}/notifications", "").split(",") if n.strip()]
        }
        return config
    except Exception as e:
        logging.error(f"Error parsing annotations: {e}")
        return None

def sync_monitor(api, monitor_name, config, logger):
    monitors = api.get_monitors()
    existing = next((m for m in monitors if m['name'] == monitor_name), None)

    type_map = {
        "http": MonitorType.HTTP,
        "port": MonitorType.PORT,
        "ping": MonitorType.PING,
        "dns": MonitorType.DNS
    }

    notification_ids = []
    if config["notifications"]:
        try:
            all_notifs = api.get_notifications()
            for n_name in config["notifications"]:
                notif = next((n for n in all_notifs if n['name'] == n_name), None)
                if notif:
                    notification_ids.append(notif['id'])
                else:
                    logger.warning(f"Notification group '{n_name}' not found")
        except Exception as e:
            logger.error(f"Notification fetch error: {e}")

    args = {
        "type": type_map.get(config["type"], MonitorType.HTTP),
        "name": monitor_name,
        "interval": config["interval"],
        "maxretries": config["maxretries"],
        "notificationIDList": notification_ids
    }

    if config["type"] == "http":
        if not config["url"]:
             logger.error(f"URL missing for {monitor_name}")
             return
        args["url"] = config["url"]
    else:
        if not config["hostname"]:
             logger.error(f"Hostname missing for {monitor_name}")
             return
        args["hostname"] = config["hostname"]
        if config["type"] == "port":
            args["port"] = config["port"]

    if existing:
        logger.info(f"UPDATING monitor: {monitor_name}")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"CREATING monitor: {monitor_name}")
        api.add_monitor(**args)

@kopf.on.startup()
def on_startup(logger, settings: kopf.OperatorSettings, **kwargs):
    settings.peering.standalone = True
    settings.peering.name = "standalone"
    
    logger.info("KumaOps Operator Startup")
    if not all([KUMA_URL, KUMA_USER, KUMA_PASS]):
        logger.error("Missing KUMA_URL, KUMA_USER, or KUMA_PASS environment variables.")
        return

    try:
        kuma_manager.get_api()
        logger.info("Uptime Kuma connection verified.")
    except Exception as e:
        logger.error(f"Failed initial connection: {e}")

@kopf.on.resume('apps', 'v1', 'deployments')
@kopf.on.create('apps', 'v1', 'deployments')
@kopf.on.update('apps', 'v1', 'deployments')
def reconcile(name, namespace, annotations, logger, **kwargs):
    logger.debug(f"Event for {namespace}/{name}")
    monitor_name = get_monitor_name(name, namespace)
    config = parse_annotations(annotations)
    
    try:
        api = kuma_manager.get_api()
        if config:
            logger.info(f"Reconciling deployment: {namespace}/{name}")
            sync_monitor(api, monitor_name, config, logger)
        else:
            # Check if it was previously enabled and needs deletion
            monitors = api.get_monitors()
            existing = next((m for m in monitors if m['name'] == monitor_name), None)
            if existing:
                logger.info(f"Removing monitor for disabled deployment: {monitor_name}")
                api.delete_monitor(existing['id'])
    except Exception as e:
        logger.error(f"Reconciliation failure for {monitor_name}: {e}")

@kopf.on.delete('apps', 'v1', 'deployments')
def on_delete(name, namespace, logger, **kwargs):
    monitor_name = get_monitor_name(name, namespace)
    logger.info(f"Deployment {namespace}/{name} deleted.")
    try:
        api = kuma_manager.get_api()
        monitors = api.get_monitors()
        existing = next((m for m in monitors if m['name'] == monitor_name), None)
        if existing:
            logger.info(f"Deleting monitor: {monitor_name}")
            api.delete_monitor(existing['id'])
    except Exception as e:
        logger.error(f"Cleanup error for {monitor_name}: {e}")
