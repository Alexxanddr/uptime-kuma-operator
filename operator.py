import os
import kopf
import logging
import sys
import threading
import time
import urllib.parse
from collections.abc import Mapping
from uptime_kuma_api import UptimeKumaApi, MonitorType

# Wrap UptimeKumaApi monitor conversion to prevent KeyError on non-HTTP monitors
try:
    import uptime_kuma_api.api as _kuma_api_mod

    _orig_convert_monitor_input = _kuma_api_mod._convert_monitor_input
    def _safe_convert_monitor_input(kwargs) -> None:
        if kwargs is not None and isinstance(kwargs, dict):
            kwargs.setdefault("accepted_statuscodes", ["200-299"])
            kwargs.setdefault("notificationIDList", [])
            kwargs.setdefault("databaseConnectionString", None)
            kwargs.setdefault("pushToken", None)
            kwargs.setdefault("dns_resolve_type", "A")
        _orig_convert_monitor_input(kwargs)

    _kuma_api_mod._convert_monitor_input = _safe_convert_monitor_input
except Exception as _e:
    logging.debug(f"Could not wrap _convert_monitor_input: {_e}")

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

def extract_effective_annotations(body=None, annotations=None):
    merged = {}
    # 1. Pod template annotations in body: spec.template.metadata.annotations
    if body and (isinstance(body, Mapping) or hasattr(body, 'get')):
        try:
            spec = body.get('spec') if hasattr(body, 'get') else None
            if spec and (isinstance(spec, Mapping) or hasattr(spec, 'get')):
                template = spec.get('template') if hasattr(spec, 'get') else None
                if template and (isinstance(template, Mapping) or hasattr(template, 'get')):
                    tmpl_meta = template.get('metadata') if hasattr(template, 'get') else None
                    if tmpl_meta and (isinstance(tmpl_meta, Mapping) or hasattr(tmpl_meta, 'get')):
                        tmpl_annotations = tmpl_meta.get('annotations') if hasattr(tmpl_meta, 'get') else None
                        if tmpl_annotations:
                            if hasattr(tmpl_annotations, 'items'):
                                merged.update(dict(tmpl_annotations.items()))
                            elif isinstance(tmpl_annotations, dict):
                                merged.update(tmpl_annotations)
        except Exception as e:
            logging.debug(f"Could not read template annotations: {e}")

        try:
            meta = body.get('metadata') if hasattr(body, 'get') else None
            if meta and (isinstance(meta, Mapping) or hasattr(meta, 'get')):
                body_annotations = meta.get('annotations') if hasattr(meta, 'get') else None
                if body_annotations:
                    if hasattr(body_annotations, 'items'):
                        merged.update(dict(body_annotations.items()))
                    elif isinstance(body_annotations, dict):
                        merged.update(body_annotations)
        except Exception as e:
            logging.debug(f"Could not read metadata annotations from body: {e}")

    # 2. Direct annotations parameter (top-level deployment metadata.annotations)
    # This guarantees 100% backward compatibility with previous behavior
    if annotations is not None:
        if hasattr(annotations, 'items'):
            merged.update(dict(annotations.items()))
        elif isinstance(annotations, dict):
            merged.update(annotations)

    return merged

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

def extract_tcp_host_and_port(annotations, name=None, namespace=None):
    """
    Robustly extracts clean hostname and integer port for TCP monitors.
    Supports:
    - uptime-kuma.io/port
    - uptime-kuma.io/hostname (e.g. 'db', 'db:5432', 'tcp://db:5432')
    - uptime-kuma.io/host
    - uptime-kuma.io/url
    - In-cluster default: {name}.{namespace}.svc.cluster.local:80
    """
    raw_port = (
        annotations.get(f"{ANNOTATION_PREFIX}/port")
        or annotations.get("port")
    )
    port = None
    if raw_port:
        try:
            p = int(str(raw_port).strip())
            if 1 <= p <= 65535:
                port = p
        except ValueError:
            pass

    raw_target = (
        annotations.get(f"{ANNOTATION_PREFIX}/hostname")
        or annotations.get(f"{ANNOTATION_PREFIX}/host")
        or annotations.get(f"{ANNOTATION_PREFIX}/url")
        or ""
    ).strip()

    hostname = None
    if raw_target:
        if "://" in raw_target:
            target_str = raw_target
        else:
            target_str = "//" + raw_target
        try:
            parsed = urllib.parse.urlsplit(target_str)
            hostname = parsed.hostname
            if parsed.port and not port:
                port = parsed.port
        except Exception:
            hostname = raw_target

    if hostname and ":" in hostname:
        parts = hostname.split(":")
        hostname = parts[0]
        if not port and len(parts) > 1 and parts[1].isdigit():
            try:
                p = int(parts[1])
                if 1 <= p <= 65535:
                    port = p
            except ValueError:
                pass

    if not hostname:
        if name and namespace:
            hostname = f"{name}.{namespace}.svc.cluster.local"
        elif name:
            hostname = name

    if not port:
        port = 80

    return hostname, int(port)

def extract_host_target(annotations, name=None, namespace=None):
    """Extracts clean hostname for DNS or Ping monitors."""
    raw_target = (
        annotations.get(f"{ANNOTATION_PREFIX}/hostname")
        or annotations.get(f"{ANNOTATION_PREFIX}/host")
        or annotations.get(f"{ANNOTATION_PREFIX}/url")
        or ""
    ).strip()

    hostname = None
    if raw_target:
        if "://" in raw_target:
            target_str = raw_target
        else:
            target_str = "//" + raw_target
        try:
            parsed = urllib.parse.urlsplit(target_str)
            hostname = parsed.hostname
        except Exception:
            hostname = raw_target

    if hostname and ":" in hostname:
        hostname = hostname.split(":")[0]

    if not hostname:
        if name and namespace:
            hostname = f"{name}.{namespace}.svc.cluster.local"
        elif name:
            hostname = name

    return hostname

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

    raw_type = str(annotations.get(f"{ANNOTATION_PREFIX}/type", "http")).lower().strip()
    if raw_type in ("tcp", "port", "tcp-port", "tcpport"):
        monitor_type = "tcp"
    elif raw_type in ("dns",):
        monitor_type = "dns"
    elif raw_type in ("ping", "icmp"):
        monitor_type = "ping"
    else:
        monitor_type = "http"

    try:
        url = None
        hostname = None
        port = None
        dns_server = None

        if monitor_type == "tcp":
            hostname, port = extract_tcp_host_and_port(annotations, name=name, namespace=namespace)
        elif monitor_type in ("dns", "ping"):
            hostname = extract_host_target(annotations, name=name, namespace=namespace)
            raw_port = annotations.get(f"{ANNOTATION_PREFIX}/port")
            port = int(str(raw_port).strip()) if raw_port and str(raw_port).strip().isdigit() else (53 if monitor_type == "dns" else None)
            if monitor_type == "dns":
                dns_server = annotations.get(f"{ANNOTATION_PREFIX}/dns-server") or annotations.get(f"{ANNOTATION_PREFIX}/dns-resolve-server") or "1.1.1.1"
        else:
            url = annotations.get(f"{ANNOTATION_PREFIX}/url")
            if not url:
                raw_host = annotations.get(f"{ANNOTATION_PREFIX}/hostname") or annotations.get(f"{ANNOTATION_PREFIX}/host")
                if raw_host:
                    url = f"http://{raw_host}"
                elif name and namespace:
                    url = f"http://{name}.{namespace}.svc.cluster.local"

        config = {
            "name": custom_name or default_name,
            "default_name": default_name,
            "namespace": namespace,
            "resource_name": name,
            "type": monitor_type,
            "url": url,
            "hostname": hostname,
            "port": port,
            "dns_server": dns_server,
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

    target_type = type_map.get(config["type"], MonitorType.HTTP)
    args = {
        "type": target_type,
        "name": monitor_name,
        "description": k8s_tag,
        "interval": config["interval"],
        "maxretries": config["maxretries"],
        "notificationIDList": notification_ids,
        "parent": parent_id
    }

    if config["type"] == "http":
        if not config.get("url"):
             logger.error(f"URL missing for {monitor_name}")
             return
        args["url"] = config["url"]
        args["accepted_statuscodes"] = config.get("accepted_statuscodes") or ["200-299"]
    elif config["type"] in ("tcp", "port"):
        if not config.get("hostname"):
             logger.error(f"Hostname missing for TCP monitor {monitor_name}")
             return
        args["hostname"] = config["hostname"]
        args["port"] = int(config.get("port") or 80)
    elif config["type"] == "dns":
        if not config.get("hostname"):
             logger.error(f"Hostname missing for DNS monitor {monitor_name}")
             return
        args["hostname"] = config["hostname"]
        args["port"] = int(config.get("port") or 53)
        args["dns_resolve_server"] = config.get("dns_server") or "1.1.1.1"
        args["dns_resolve_type"] = "A"
    elif config["type"] == "ping":
        if not config.get("hostname"):
             logger.error(f"Hostname missing for Ping monitor {monitor_name}")
             return
        args["hostname"] = config["hostname"]

    if existing:
        existing_type = existing.get('type')
        # If monitor type changed (e.g. from HTTP to TCP/PORT), delete old monitor and recreate
        # to ensure clean schema and avoid corrupt parameter state in Uptime Kuma
        if existing_type != target_type and str(existing_type).lower() != str(target_type).lower():
            logger.info(f"Monitor type changed from '{existing_type}' to '{target_type}'. Recreating monitor: {monitor_name}")
            try:
                api.delete_monitor(existing['id'])
            except Exception as e:
                logger.warning(f"Error deleting monitor {existing['id']} during type change: {e}")
            existing = None

    if existing:
        logger.info(f"UPDATING monitor: {monitor_name} (type: {config['type']})")
        api.edit_monitor(existing['id'], **args)
    else:
        logger.info(f"CREATING monitor: {monitor_name} (type: {config['type']})")
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

@kopf.on.resume('apps', 'v1', 'deployments', id='resume-deployments')
@kopf.on.resume('apps', 'v1', 'statefulsets', id='resume-statefulsets')
@kopf.on.resume('apps', 'v1', 'daemonsets', id='resume-daemonsets')
@kopf.on.create('apps', 'v1', 'deployments', id='create-deployments')
@kopf.on.create('apps', 'v1', 'statefulsets', id='create-statefulsets')
@kopf.on.create('apps', 'v1', 'daemonsets', id='create-daemonsets')
@kopf.on.update('apps', 'v1', 'deployments', id='update-deployments')
@kopf.on.update('apps', 'v1', 'statefulsets', id='update-statefulsets')
@kopf.on.update('apps', 'v1', 'daemonsets', id='update-daemonsets')
def reconcile(name, namespace, annotations, logger, old=None, body=None, **kwargs):
    kind = (body.get('kind') if hasattr(body, 'get') else None) or kwargs.get('resource', {}).get('kind', 'Resource')
    logger.debug(f"Event for {kind} {namespace}/{name}")
    effective_annotations = extract_effective_annotations(body=body, annotations=annotations)
    config = parse_annotations(effective_annotations, name=name, namespace=namespace)
    default_name = f"k8s-{namespace}-{name}"
    k8s_tag = f"k8s:{namespace}/{name}"
    svc_pattern = f"{name}.{namespace}.svc"

    # Extract any previous custom name from 'old' state
    old_obj = old if old is not None else kwargs.get('old')
    old_annotations = extract_effective_annotations(body=old_obj)
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
                    and field_path[-1] == f"{ANNOTATION_PREFIX}/name"
                    and old_val
                ):
                    diff_old_names.add(str(old_val).strip())

    try:
        api = kuma_manager.get_api()
        if config:
            if old_custom_name and old_custom_name != config["name"]:
                config["old_name"] = old_custom_name
            monitor_name = config["name"]
            logger.info(f"Reconciling {kind.lower()}: {namespace}/{name} -> monitor: '{monitor_name}'")
            sync_monitor(api, monitor_name, config, logger)
        else:
            # Resource has annotations removed or disabled -> delete associated monitor(s)
            candidates = {default_name}
            current_custom_name = effective_annotations.get(f"{ANNOTATION_PREFIX}/name", "").strip() if effective_annotations else None
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
                or (m.get('hostname') and svc_pattern in m['hostname'])
            ]
            for m in to_delete:
                logger.info(f"Removing monitor for disabled {kind.lower()} {namespace}/{name}: '{m['name']}' (ID: {m['id']})")
                api.delete_monitor(m['id'])
    except Exception as e:
        logger.error(f"Reconciliation failure for {kind.lower()} {namespace}/{name}: {e}")
        raise kopf.TemporaryError(f"Reconciliation failure for {kind.lower()} {namespace}/{name}: {e}", delay=15)

@kopf.on.delete('apps', 'v1', 'deployments', id='delete-deployments')
@kopf.on.delete('apps', 'v1', 'statefulsets', id='delete-statefulsets')
@kopf.on.delete('apps', 'v1', 'daemonsets', id='delete-daemonsets')
def on_delete(name, namespace, annotations, logger, body=None, **kwargs):
    kind = (body.get('kind') if hasattr(body, 'get') else None) or kwargs.get('resource', {}).get('kind', 'Resource')
    effective_annotations = extract_effective_annotations(body=body, annotations=annotations)
    default_name = f"k8s-{namespace}-{name}"
    custom_name = effective_annotations.get(f"{ANNOTATION_PREFIX}/name", "").strip() if effective_annotations else None
    k8s_tag = f"k8s:{namespace}/{name}"
    svc_pattern = f"{name}.{namespace}.svc"
    candidates = {default_name}
    if custom_name:
        candidates.add(custom_name)

    logger.info(f"{kind} {namespace}/{name} deleted.")
    try:
        api = kuma_manager.get_api()
        monitors = api.get_monitors()
        to_delete = [
            m for m in monitors
            if (m.get('description') and m['description'].startswith(k8s_tag))
            or m.get('name') in candidates
            or (m.get('url') and svc_pattern in m['url'])
            or (m.get('hostname') and svc_pattern in m['hostname'])
        ]
        for m in to_delete:
            logger.info(f"Deleting monitor for deleted {kind.lower()} {namespace}/{name}: '{m['name']}' (ID: {m['id']})")
            api.delete_monitor(m['id'])
    except Exception as e:
        logger.error(f"Cleanup error for {kind.lower()} {namespace}/{name}: {e}")
        raise kopf.TemporaryError(f"Cleanup error for {kind.lower()} {namespace}/{name}: {e}", delay=15)
