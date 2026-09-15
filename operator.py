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

def get_monitor_name(name, namespace, annotations=None):
    if annotations:
        custom_name = annotations.get(f"{ANNOTATION_PREFIX}/name")
        if custom_name and custom_name.strip():
            return custom_name.strip()
    return f"k8s-{namespace}-{name}"

def parse_accepted_status_codes(val):
    if not val:
        return None
    import json
    val = str(val).strip()
    raw_tokens = []
    if val.startswith('[') and val.endswith(']'):
        try:
            parsed = json.loads(val)
            raw_tokens = [str(x).strip() for x in parsed]
        except Exception:
            raw_tokens = [x.strip() for x in val.strip('[]').split(',')]
    else:
        raw_tokens = [x.strip() for x in val.split(',')]
    
    allowed = {'100-199', '200-299', '300-399', '400-499', '500-599'} | {str(i) for i in range(100, 1000)}
    result = []
    for token in raw_tokens:
        token = token.strip('\"\' ')
        if not token:
            continue
        if token in allowed:
            if token not in result:
                result.append(token)
        elif '-' in token:
            parts = token.split('-')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                if 100 <= start <= 999 and 100 <= end <= 999 and start <= end:
                    if end - start <= 20:
                        for c in range(start, end + 1):
                            s = str(c)
                            if s not in result:
                                result.append(s)
                    else:
                        for century in range((start // 100) * 100, (end // 100 + 1) * 100, 100):
                            r = f"{century}-{century+99}"
                            if r in allowed and r not in result:
                                result.append(r)
    return result if result else None

def parse_annotations(annotations, name=None, namespace=None):
    if not annotations:
        return None
    
    enabled_val = str(annotations.get(f"{ANNOTATION_PREFIX}/enabled", "false")).lower()
    if enabled_val != "true":
        return None
    
    default_name = f"k8s-{namespace}-{name}" if name and namespace else None
    custom_name = annotations.get(f"{ANNOTATION_PREFIX}/name", "").strip() or None

    status_codes_raw = (
        annotations.get(f"{ANNOTATION_PREFIX}/status-codes")
        or annotations.get(f"{ANNOTATION_PREFIX}/accepted-statuscodes")
        or annotations.get(f"{ANNOTATION_PREFIX}/accepted-status-codes")
        or annotations.get(f"{ANNOTATION_PREFIX}/statuscodes")
    )
    accepted_statuscodes = parse_accepted_status_codes(status_codes_raw)

    try:
        config = {
            "name": custom_name or default_name,
            "default_name": default_name,
            "namespace": namespace,
            "resource_name": name,
            "type": annotations.get(f"{ANNOTATION_PREFIX}/type", "http").lower(),
            "url": annotations.get(f"{ANNOTATION_PREFIX}/url"),
            "hostname": annotations.get(f"{ANNOTATION_PREFIX}/hostname"),
            "port": int(annotations.get(f"{ANNOTATION_PREFIX}/port", 80)) if annotations.get(f"{ANNOTATION_PREFIX}/port") else None,
            "interval": int(annotations.get(f"{ANNOTATION_PREFIX}/interval", 60)),
            "maxretries": int(annotations.get(f"{ANNOTATION_PREFIX}/retries", 3)),
            "notifications": [n.strip() for n in annotations.get(f"{ANNOTATION_PREFIX}/notifications", "").split(",") if n.strip()],
            "group": annotations.get(f"{ANNOTATION_PREFIX}/group"),
            "accepted_statuscodes": accepted_statuscodes
        }
        return config
    except Exception as e:
        logging.error(f"Error parsing annotations: {e}")
        return None

def sync_monitor(api, monitor_name, config, logger):
    monitors = api.get_monitors()
    ns = config.get("namespace") or ""
    res_name = config.get("resource_name") or ""
    k8s_tag = f"k8s:{ns}/{res_name}" if ns and res_name else None

    # Check if monitor exists:
    # 1. By k8s_tag in description
    existing = None
    if k8s_tag:
        existing = next((m for m in monitors if m.get('description') and m['description'].startswith(k8s_tag)), None)

    # 2. Check if monitor exists by custom/target name
    if not existing:
        existing = next((m for m in monitors if m['name'] == monitor_name), None)

    # 3. Check if monitor exists by old name (if renamed)
    if not existing and config.get("old_name") and config["old_name"] != monitor_name:
        existing = next((m for m in monitors if m['name'] == config["old_name"]), None)

    # 4. Check fallback default name
    if not existing and config.get("default_name") and config["default_name"] != monitor_name:
        existing = next((m for m in monitors if m['name'] == config["default_name"]), None)

    type_map = {
        "http": MonitorType.HTTP,
        "tcp": MonitorType.PORT,
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

    # Resolve parent group ID if specified
    parent_id = None
    if config.get("group"):
        group_name = config["group"].strip()
        try:
            groups = [m for m in monitors if m.get('type') == 'group']
            group_obj = next((g for g in groups if g.get('name') == group_name or str(g.get('id')) == group_name), None)
            if group_obj:
                parent_id = group_obj['id']
            else:
                logger.warning(f"Group '{group_name}' not found in Uptime Kuma.")
        except Exception as e:
            logger.error(f"Error resolving group '{group_name}': {e}")

    args = {
        "type": type_map.get(config["type"], MonitorType.HTTP),
        "name": monitor_name,
        "description": k8s_tag,
        "interval": config["interval"],
        "maxretries": config["maxretries"],
        "notificationIDList": notification_ids,
        "parent": parent_id
    }

    if config["type"] == "http":
        if not config["url"]:
             logger.error(f"URL missing for {monitor_name}")
             return
        args["url"] = config["url"]
        args["accepted_statuscodes"] = config.get("accepted_statuscodes") or ["200-299"]
    else:
        if not config["hostname"]:
             logger.error(f"Hostname missing for {monitor_name}")
             return
        args["hostname"] = config["hostname"]
        if config["type"] == "tcp":
            args["port"] = config["port"] or 80

    if existing:
        logger.info(f"UPDATING monitor: {monitor_name}")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"CREATING monitor: {monitor_name}")
        try:
            api.add_monitor(**args, conditions=[])
        except TypeError:
            api.add_monitor(**args)

@kopf.on.login()
def login_fn(**kwargs):
    return (
        kopf.login_with_service_account(**kwargs)
        or kopf.login_via_client(**kwargs)
        or kopf.login_with_kubeconfig(**kwargs)
    )

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
def reconcile(name, namespace, annotations, logger, old=None, **kwargs):
    logger.debug(f"Event for {namespace}/{name}")
    config = parse_annotations(annotations, name=name, namespace=namespace)
    default_name = f"k8s-{namespace}-{name}"
    k8s_tag = f"k8s:{namespace}/{name}"
    svc_pattern = f"{name}.{namespace}.svc"

    # Extract any previous custom name from 'old' state
    old_annotations = {}
    if isinstance(old, dict):
        old_annotations = old.get('metadata', {}).get('annotations') or {}
    elif 'old' in kwargs and isinstance(kwargs['old'], dict):
        old_annotations = kwargs['old'].get('metadata', {}).get('annotations') or {}

    old_custom_name = old_annotations.get(f"{ANNOTATION_PREFIX}/name", "").strip() if old_annotations else None

    # Also inspect 'diff' for any removed or changed name annotations
    diff_old_names = set()
    diff = kwargs.get('diff')
    if diff:
        for item in diff:
            if len(item) >= 3:
                field_path = item[1]
                old_val = item[2]
                if (
                    isinstance(field_path, (list, tuple))
                    and len(field_path) >= 3
                    and field_path[0] == 'metadata'
                    and field_path[1] == 'annotations'
                    and field_path[2] == f"{ANNOTATION_PREFIX}/name"
                    and old_val
                ):
                    diff_old_names.add(str(old_val).strip())

    try:
        api = kuma_manager.get_api()
        if config:
            if old_custom_name and old_custom_name != config["name"]:
                config["old_name"] = old_custom_name
            monitor_name = config["name"]
            logger.info(f"Reconciling deployment: {namespace}/{name} -> monitor: '{monitor_name}'")
            sync_monitor(api, monitor_name, config, logger)
        else:
            # Deployment has annotations removed or disabled -> delete associated monitor(s)
            candidates = {default_name}
            current_custom_name = annotations.get(f"{ANNOTATION_PREFIX}/name", "").strip() if annotations else None
            if current_custom_name:
                candidates.add(current_custom_name)
            if old_custom_name:
                candidates.add(old_custom_name)
            candidates.update(diff_old_names)

            monitors = api.get_monitors()
            to_delete = [
                m for m in monitors
                if (m.get('description') and m['description'].startswith(k8s_tag))
                or m.get('name') in candidates
                or (m.get('url') and svc_pattern in m['url'])
            ]
            for m in to_delete:
                logger.info(f"Removing monitor for disabled deployment {namespace}/{name}: '{m['name']}' (ID: {m['id']})")
                api.delete_monitor(m['id'])
    except Exception as e:
        logger.error(f"Reconciliation failure for {namespace}/{name}: {e}")
        raise kopf.TemporaryError(f"Reconciliation failure for {namespace}/{name}: {e}", delay=15)

@kopf.on.delete('apps', 'v1', 'deployments')
def on_delete(name, namespace, annotations, logger, **kwargs):
    default_name = f"k8s-{namespace}-{name}"
    custom_name = annotations.get(f"{ANNOTATION_PREFIX}/name", "").strip() if annotations else None
    k8s_tag = f"k8s:{namespace}/{name}"
    svc_pattern = f"{name}.{namespace}.svc"
    candidates = {default_name}
    if custom_name:
        candidates.add(custom_name)

    logger.info(f"Deployment {namespace}/{name} deleted.")
    try:
        api = kuma_manager.get_api()
        monitors = api.get_monitors()
        to_delete = [
            m for m in monitors
            if (m.get('description') and m['description'].startswith(k8s_tag))
            or m.get('name') in candidates
            or (m.get('url') and svc_pattern in m['url'])
        ]
        for m in to_delete:
            logger.info(f"Deleting monitor for deleted deployment {namespace}/{name}: '{m['name']}' (ID: {m['id']})")
            api.delete_monitor(m['id'])
    except Exception as e:
        logger.error(f"Cleanup error for {namespace}/{name}: {e}")
        raise kopf.TemporaryError(f"Cleanup error for {namespace}/{name}: {e}", delay=15)
