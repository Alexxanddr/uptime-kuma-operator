import os
import kopf
import logging
import sys
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
# Force kopf logs to respect our level too
logging.getLogger('kopf').setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

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

class KumaSession:
    def __init__(self):
        self.api = None

    def __enter__(self):
        try:
            self.api = UptimeKumaApi(KUMA_URL)
            self.api.login(KUMA_USER, KUMA_PASS)
            return self.api
        except Exception as e:
            logging.error(f"Uptime Kuma Login Error: {e}")
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.api:
            try:
                self.api.disconnect()
            except:
                pass

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
        args["url"] = config["url"]
    else:
        args["hostname"] = config["hostname"]
        if config["type"] == "port":
            args["port"] = config["port"]

    if existing:
        logger.info(f"UPDATING {monitor_name} (ID: {existing['id']})")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"CREATING {monitor_name}")
        api.add_monitor(**args)

@kopf.on.startup()
def on_startup(logger, **kwargs):
    logger.info("KumaOps Operator Startup")
    logger.info(f"Kuma URL: {KUMA_URL} | Log Level: {LOG_LEVEL}")
    
    if not all([KUMA_URL, KUMA_USER, KUMA_PASS]):
        logger.error("Missing KUMA_URL, KUMA_USER, or KUMA_PASS")
        return

    try:
        with KumaSession() as api:
            logger.info("Connection to Uptime Kuma verified.")
    except Exception as e:
        logger.error(f"Startup connection failed: {e}")

@kopf.on.resume('apps', 'v1', 'deployments')
@kopf.on.create('apps', 'v1', 'deployments')
@kopf.on.update('apps', 'v1', 'deployments')
def reconcile(name, namespace, annotations, logger, **kwargs):
    logger.debug(f"Event for {namespace}/{name}")
    monitor_name = get_monitor_name(name, namespace)
    config = parse_annotations(annotations)
    
    try:
        with KumaSession() as api:
            if config:
                logger.info(f"Reconciling {namespace}/{name} (ENABLED)")
                sync_monitor(api, monitor_name, config, logger)
            else:
                # Clean up or ignore
                existing = next((m for m in api.get_monitors() if m['name'] == monitor_name), None)
                if existing:
                    logger.info(f"Removing monitor for disabled deployment: {monitor_name}")
                    api.delete_monitor(existing['id'])
    except Exception as e:
        logger.error(f"Reconciliation failure for {monitor_name}: {e}")

@kopf.on.delete('apps', 'v1', 'deployments')
def on_delete(name, namespace, logger, **kwargs):
    monitor_name = get_monitor_name(name, namespace)
    try:
        with KumaSession() as api:
            existing = next((m for m in api.get_monitors() if m['name'] == monitor_name), None)
            if existing:
                logger.info(f"Deleting monitor on deployment removal: {monitor_name}")
                api.delete_monitor(existing['id'])
    except Exception as e:
        logger.error(f"Deletion failure for {monitor_name}: {e}")
