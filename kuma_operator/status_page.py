"""Conservative whole-page updates for explicitly reserved groups on Kuma 2.5.8."""

import copy
import json
import uuid
import requests
from .config import InvalidConfig


def read(api, settings):
    # The client drops autoRefreshInterval on save and uses a cached public read.
    # Keep the authenticated configuration intact and bypass the public cache.
    if str(api._parsed_version()) != "2.5.8":
        raise InvalidConfig("Status page synchronization requires tested Kuma 2.5.8")
    config = api._call("getStatusPage", settings.slug)["config"]
    if config["id"] != settings.id or config["slug"] != settings.slug:
        raise InvalidConfig("Status page identity changed")
    response = requests.get(
        f"{api.url}/api/status-page/{settings.slug}",
        params={"kumaops": uuid.uuid4().hex},
        timeout=api.timeout,
        verify=api.ssl_verify,
        headers=api.headers,
    )
    response.raise_for_status()
    public = response.json()
    if any(config.get(k) != v for k, v in public["config"].items()):
        raise InvalidConfig("Status page changed during read; retry")
    return config, public["publicGroupList"]


def editable(groups):
    """Retain relationship fields, not computed monitor names, types or statistics."""
    return [
        {
            "id": group["id"],
            "name": group["name"],
            "monitorList": [
                {k: monitor[k] for k in ("id", "sendUrl", "url") if k in monitor}
                for monitor in group["monitorList"]
            ],
        }
        for group in groups
    ]


def owned_marker(value):
    try:
        parts = json.loads(value.removeprefix("k8s:v2:"))
        return (
            value.startswith("k8s:v2:")
            and isinstance(parts, list)
            and len(parts) == 4
            and all(isinstance(p, str) and p for p in parts)
        )
    except (ValueError, TypeError, AttributeError):
        return False


def path(monitors, group_id):
    names, seen = [], set()
    while group_id:
        if group_id in seen or group_id not in monitors or monitors[group_id].get("type") != "group":
            raise InvalidConfig("Invalid Kuma group hierarchy")
        seen.add(group_id)
        group = monitors[group_id]
        names.append(group["name"])
        group_id = int(group.get("parent") or 0)
    return " / ".join(reversed(names)) or "Ungrouped"


def sync(api, settings, monitor, monitors, include):
    config, observed = read(api, settings)
    original = editable(observed)
    desired = copy.deepcopy(original)
    targets = {g["id"]: g for g in desired}
    if not set(settings.groups.values()) <= targets.keys():
        raise InvalidConfig("Reserved status page group missing; do not recreate or adopt by name")
    by_id = {m["id"]: m for m in monitors}
    # Reserved groups own membership/link settings for UID-marked monitors only.
    # Public Kuma reads cannot recover a custom URL hidden behind sendUrl=false.
    for group in original:
        for item in group["monitorList"]:
            reserved = group["id"] in settings.groups.values()
            managed = owned_marker(by_id.get(item["id"], {}).get("description", ""))
            if reserved and managed:
                if item.get("sendUrl") or item.get("url"):
                    raise InvalidConfig("Reserved status page association has manual link settings")
            elif not item.get("sendUrl") or "url" not in item:
                raise InvalidConfig("Manual association may contain a hidden URL; refusing lossy save")
    for source, target in settings.groups.items():
        if not source or source in by_id:
            targets[target]["name"] = path(by_id, source)
    for group in desired:
        if group["id"] in settings.groups.values():
            group["monitorList"] = [m for m in group["monitorList"] if m["id"] != monitor["id"]]
    if include and monitor.get("active", True):
        parent = int(monitor.get("parent") or 0)
        if parent not in settings.groups:
            raise InvalidConfig("Status page parent group has no explicit mapping")
        target = targets[settings.groups[parent]]
        previous = next(g for g in original if g["id"] == target["id"])["monitorList"]
        index = next((i for i, m in enumerate(previous) if m["id"] == monitor["id"]), len(previous))
        target["monitorList"].insert(index, {"id": monitor["id"], "sendUrl": False})
    if original == desired:
        return "unchanged"
    if read(api, settings) != (config, observed):
        raise InvalidConfig("Status page changed before save; retry")
    # shortcut: Kuma has no CAS, require one writer; upgrade when upstream adds revisions.
    api._call("saveStatusPage", (settings.slug, config, config["icon"], desired))
    saved_config, saved_groups = read(api, settings)
    if saved_config != config or editable(saved_groups) != desired:
        raise InvalidConfig("Status page save incomplete; reread and reconcile")
    return "updated"
