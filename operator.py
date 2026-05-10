import os
import kopf
import logging
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
    if not annotations or annotations.get(f"{ANNOTATION_PREFIX}/enabled") != "true":
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
        all_notifs = api.get_notifications()
        for n_name in config["notifications"]:
            n_name = n_name.strip()
            if not n_name: continue
            notif = next((n for n in all_notifs if n['name'] == n_name), None)
            if notif:
                notification_ids.append(notif['id'])

    # Build common monitor arguments
    args = {
        "type": type_map.get(config["type"], MonitorType.HTTP),
        "name": monitor_name,
        "interval": config["interval"],
        "maxretries": config["maxretries"],
        "notificationIDList": notification_ids
    }

    # Set type-specific fields
    m_type = config["type"]
    if m_type == "http":
        args["url"] = config["url"]
    elif m_type in ["port", "ping", "dns"]:
        args["hostname"] = config["hostname"]
        if m_type == "port":
            args["port"] = config["port"]

    if existing:
        logger.info(f"Updating existing monitor: {monitor_name}")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"Creating new monitor: {monitor_name}")
        api.add_monitor(**args)

@kopf.on.create('deployments')
@kopf.on.update('deployments')
def reconcile(name, namespace, annotations, logger, **kwargs):
    """Reconcile Deployment annotations with Uptime Kuma monitors."""
    config = parse_annotations(annotations)
    monitor_name = get_monitor_name(name, namespace)
    try:
        with get_api() as api:
            if config:
                sync_monitor(api, monitor_name, config, logger)
            else:
                # Monitoring disabled or annotations removed
                delete_monitor_logic(api, monitor_name, logger)
    except Exception as e:
        logger.error(f"Reconciliation failed for {monitor_name}: {str(e)}")

@kopf.on.delete('deployments')
def on_delete(name, namespace, logger, **kwargs):
    """Delete the monitor when the Deployment is deleted."""
    monitor_name = get_monitor_name(name, namespace)
    try:
        with get_api() as api:
            delete_monitor_logic(api, monitor_name, logger)
    except Exception as e:
        logger.error(f"Deletion failed for {monitor_name}: {str(e)}")

def delete_monitor_logic(api, monitor_name, logger):
    """Internal logic to delete a monitor by name."""
    monitors = api.get_monitors()
    existing = next((m for m in monitors if m['name'] == monitor_name), None)
    if existing:
        api.delete_monitor(existing['id'])
        logger.info(f"Monitor {monitor_name} deleted successfully.")
