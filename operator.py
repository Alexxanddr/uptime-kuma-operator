import os
import kopf
import logging
import sys
from uptime_kuma_api import UptimeKumaApi, MonitorType

# Configuration from Environment Variables
KUMA_URL = os.getenv("KUMA_URL")
KUMA_USER = os.getenv("KUMA_USER")
KUMA_PASS = os.getenv("KUMA_PASS")

ANNOTATION_PREFIX = "uptime-kuma.io"

def get_api():
    """Authenticate with Uptime Kuma."""
    api = UptimeKumaApi(KUMA_URL)
    api.login(KUMA_USER, KUMA_PASS)
    return api

def get_monitor_name(name, namespace):
    """Naming convention for monitors: k8s-{namespace}-{name}."""
    return f"k8s-{namespace}-{name}"

def parse_annotations(annotations):
    """Extract monitor configuration from Deployment annotations."""
    if not annotations:
        return None
        
    enabled = annotations.get(f"{ANNOTATION_PREFIX}/enabled", "false").lower() == "true"
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

def sync_monitor(api, monitor_name, config, logger):
    """Create or update a monitor in Uptime Kuma."""
    monitors = api.get_monitors()
    existing = next((m for m in monitors if m['name'] == monitor_name), None)

    type_map = {
        "http": MonitorType.HTTP,
        "port": MonitorType.PORT,
        "ping": MonitorType.PING,
        "dns": MonitorType.DNS
    }

    # Resolve notification IDs by name
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
                    logger.warning(f"Notification group not found in Uptime Kuma: {n_name}")
        except Exception as e:
            logger.error(f"Failed to fetch notifications: {str(e)}")

    # Build monitor arguments
    args = {
        "type": type_map.get(config["type"], MonitorType.HTTP),
        "name": monitor_name,
        "interval": config["interval"],
        "maxretries": config["maxretries"],
        "notificationIDList": notification_ids
    }

    if config["type"] == "http":
        if not config["url"]:
            raise ValueError(f"URL is required for http type in {monitor_name}")
        args["url"] = config["url"]
    elif config["type"] in ["port", "ping", "dns"]:
        if not config["hostname"]:
            raise ValueError(f"Hostname is required for {config['type']} type in {monitor_name}")
        args["hostname"] = config["hostname"]
        if config["type"] == "port" and config["port"]:
            args["port"] = config["port"]

    if existing:
        logger.info(f"Updating existing monitor: {monitor_name} (ID: {existing['id']})")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"Creating new monitor: {monitor_name}")
        api.add_monitor(**args)

@kopf.on.startup()
def on_startup(logger, **kwargs):
    logger.info("--- KumaOps Operator Starting ---")
    logger.info(f"Target Uptime Kuma: {KUMA_URL}")
    logger.info(f"User: {KUMA_USER}")
    
    try:
        with get_api() as api:
            api.get_monitors()
            logger.info("Connection test to Uptime Kuma: SUCCESSFUL")
    except Exception as e:
        logger.error(f"Connection test to Uptime Kuma: FAILED - {str(e)}")

@kopf.on.resume('deployments')
@kopf.on.create('deployments')
@kopf.on.update('deployments')
def reconcile(name, namespace, annotations, logger, **kwargs):
    """Reconcile Deployment annotations with Uptime Kuma monitors."""
    logger.info(f"Processing Deployment: {namespace}/{name}")
    
    config = parse_annotations(annotations)
    monitor_name = get_monitor_name(name, namespace)
    
    try:
        with get_api() as api:
            if config:
                logger.info(f"Monitor enabled for {monitor_name}. Syncing...")
                sync_monitor(api, monitor_name, config, logger)
            else:
                logger.debug(f"No configuration for {monitor_name}. Ensuring it doesn't exist.")
                delete_monitor_logic(api, monitor_name, logger)
    except Exception as e:
        logger.error(f"Error during reconciliation for {monitor_name}: {str(e)}")

@kopf.on.delete('deployments')
def on_delete(name, namespace, logger, **kwargs):
    """Delete the monitor when the Deployment is deleted."""
    monitor_name = get_monitor_name(name, namespace)
    logger.info(f"Deployment {namespace}/{name} deleted. Removing monitor if exists.")
    try:
        with get_api() as api:
            delete_monitor_logic(api, monitor_name, logger)
    except Exception as e:
        logger.error(f"Error during deletion for {monitor_name}: {str(e)}")

def delete_monitor_logic(api, monitor_name, logger):
    """Internal logic to delete a monitor by name."""
    monitors = api.get_monitors()
    existing = next((m for m in monitors if m['name'] == monitor_name), None)
    if existing:
        api.delete_monitor(existing['id'])
        logger.info(f"Monitor {monitor_name} removed from Uptime Kuma.")
    else:
        logger.debug(f"Monitor {monitor_name} not found, nothing to delete.")
