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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [KumaOps] %(levelname)s: %(message)s',
    stream=sys.stdout
)

def get_monitor_name(name, namespace):
    return f"k8s-{namespace}-{name}"

def parse_annotations(annotations):
    if not annotations:
        return None
    
    enabled = str(annotations.get(f"{ANNOTATION_PREFIX}/enabled", "false")).lower() == "true"
    if not enabled:
        return None
    
    return {
        "type": annotations.get(f"{ANNOTATION_PREFIX}/type", "http").lower(),
        "url": annotations.get(f"{ANNOTATION_PREFIX}/url"),
        "hostname": annotations.get(f"{ANNOTATION_PREFIX}/hostname"),
        "port": int(annotations.get(f"{ANNOTATION_PREFIX}/port", 80)) if annotations.get(f"{ANNOTATION_PREFIX}/port") else None,
        "interval": int(annotations.get(f"{ANNOTATION_PREFIX}/interval", 60)),
        "maxretries": int(annotations.get(f"{ANNOTATION_PREFIX}/retries", 3)),
        "notifications": annotations.get(f"{ANNOTATION_PREFIX}/notifications", "").split(",")
    }

def get_connected_api():
    """Create a logged-in API instance."""
    api = UptimeKumaApi(KUMA_URL)
    api.login(KUMA_USER, KUMA_PASS)
    return api

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
                n_name = n_name.strip()
                if not n_name: continue
                notif = next((n for n in all_notifs if n['name'] == n_name), None)
                if notif:
                    notification_ids.append(notif['id'])
                else:
                    logger.warning(f"Notification group not found: {n_name}")
        except Exception as e:
            logger.error(f"Error fetching notifications: {e}")

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
        logger.info(f"UPDATING existing monitor: {monitor_name}")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"CREATING new monitor: {monitor_name}")
        api.add_monitor(**args)

@kopf.on.startup()
def on_startup(logger, **kwargs):
    logger.info("--- KumaOps Operator Warming Up ---")
    if not all([KUMA_URL, KUMA_USER, KUMA_PASS]):
        logger.error("CRITICAL: Missing Uptime Kuma credentials in environment variables.")
        return

    api = None
    try:
        api = get_connected_api()
        logger.info("Credentials verified. Connection to Uptime Kuma established.")
    except Exception as e:
        logger.error(f"Failed to verify Uptime Kuma connection: {e}")
    finally:
        if api:
            api.disconnect()

@kopf.on.resume('apps', 'v1', 'deployments')
@kopf.on.create('apps', 'v1', 'deployments')
@kopf.on.update('apps', 'v1', 'deployments')
def reconcile(name, namespace, annotations, logger, **kwargs):
    monitor_name = get_monitor_name(name, namespace)
    config = parse_annotations(annotations)
    
    api = None
    try:
        api = get_connected_api()
        if config:
            logger.info(f"Syncing Deployment {namespace}/{name}")
            sync_monitor(api, monitor_name, config, logger)
        else:
            # Clean up if previously enabled
            monitors = api.get_monitors()
            existing = next((m for m in monitors if m['name'] == monitor_name), None)
            if existing:
                logger.info(f"Removing monitor for disabled/unannotated Deployment: {monitor_name}")
                api.delete_monitor(existing['id'])
    except Exception as e:
        logger.error(f"Reconciliation failure for {monitor_name}: {e}")
    finally:
        if api:
            api.disconnect()

@kopf.on.delete('apps', 'v1', 'deployments')
def on_delete(name, namespace, logger, **kwargs):
    monitor_name = get_monitor_name(name, namespace)
    api = None
    try:
        api = get_connected_api()
        monitors = api.get_monitors()
        existing = next((m for m in monitors if m['name'] == monitor_name), None)
        if existing:
            logger.info(f"Removing monitor for deleted Deployment: {monitor_name}")
            api.delete_monitor(existing['id'])
    except Exception as e:
        logger.error(f"Cleanup failure for {monitor_name}: {e}")
    finally:
        if api:
            api.disconnect()
