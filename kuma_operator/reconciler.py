"""Ownership and idempotent reconciliation independent of Kopf."""

import json
from .config import InvalidConfig, PREFIX

MANAGED = f"{PREFIX}/managed"


class OwnershipConflict(InvalidConfig):
    pass


def identity(body):
    m = body["metadata"]
    # Kuma URL is the scope. Sharing one Kuma between clusters requires distinct
    # resource UIDs; never adopt a monitor already carrying another UID.
    return "k8s:v2:" + json.dumps([body["kind"], m["namespace"], m["name"], m["uid"]], separators=(",", ":"))


def owned_monitor(monitors, body, allow_legacy=False):
    marker = identity(body)
    exact = [m for m in monitors if m.get("description") == marker]
    if len(exact) > 1:
        raise OwnershipConflict("Multiple monitors have the same owner; manual resolution required")
    if exact:
        return exact[0]
    meta = body["metadata"]
    legacy = f"k8s:{meta['namespace']}/{meta['name']}"
    candidates = [m for m in monitors if m.get("description") == legacy]
    if candidates and allow_legacy:
        # Legacy records omit kind/UID; require explicit opt-in to claim them.
        if len(candidates) != 1:
            raise OwnershipConflict("Ambiguous legacy ownership")
        return candidates[0]
    if candidates:
        raise OwnershipConflict("Legacy monitor requires explicit adopt-legacy annotation")
    return None


def normalize(key, value):
    if key == "notificationIDList":
        if isinstance(value, dict):
            return sorted(int(k) for k, v in value.items() if v)
        return sorted(int(v) for v in (value or []))
    if key == "parent":
        return int(value) if value else None
    if key == "accepted_statuscodes":
        return sorted(value or [])
    return value


def reconcile(api, body, config, deleting=False):
    monitors = api.get_monitors()
    meta = body["metadata"]
    adopt = meta.get("annotations", {}).get(f"{PREFIX}/adopt-legacy") == "true"
    existing = owned_monitor(monitors, body, allow_legacy=adopt and not deleting and config is not None)
    if deleting or config is None:
        if existing:
            api.delete_monitor(existing["id"])
            return "deleted"
        return "absent"
    args = {k: v for k, v in config.items() if k not in ("notifications", "group")}
    args["description"] = identity(body)
    args["notificationIDList"] = []
    if config["notifications"]:
        notifications = api.get_notifications()
        for name in config["notifications"]:
            found = [n for n in notifications if n["name"] == name]
            if len(found) != 1:
                raise LookupError("Notification missing or ambiguous")
            args["notificationIDList"].append(found[0]["id"])
    args["parent"] = None
    if config["group"]:
        groups = [
            m
            for m in monitors
            if m.get("type") == "group" and (m["name"] == config["group"] or str(m["id"]) == config["group"])
        ]
        if len(groups) != 1:
            raise LookupError("Parent group missing or ambiguous")
        args["parent"] = groups[0]["id"]
    # A pre-existing duplicate display name must not prevent managing an exact
    # owner. Only creation or renaming can introduce a new name collision.
    changing_name = existing is None or existing["name"] != args["name"]
    if changing_name and any(
        m["name"] == args["name"] and (existing is None or m["id"] != existing["id"]) for m in monitors
    ):
        raise OwnershipConflict("Monitor name already in use by another owner")
    if existing:
        # get_monitor returns complete configuration, unlike some list payloads.
        observed = api.get_monitor(existing["id"])
        if all(normalize(k, observed.get(k)) == normalize(k, v) for k, v in args.items()):
            return "unchanged"
        api.edit_monitor(existing["id"], **args)
        return "updated"
    api.add_monitor(**args)
    return "created"
