"""Kopf adapters. No credentials or remote exception text enter resource state."""

import hashlib
import json
import logging
import os
from collections import Counter
import kopf
from .client import KumaManager
from .config import InvalidConfig, PREFIX, Settings, effective_annotations, parse, resync_interval
from .reconciler import MANAGED, reconcile

manager = None
outcomes = Counter()


class MonitorDiffBaseStorage(kopf.AnnotationsDiffBaseStorage):
    """Controller output must not feed back into desired-state change detection."""

    def build(self, *, body, extra_fields=None):
        essence = super().build(body=body, extra_fields=extra_fields)
        self.remove_annotations(
            essence,
            {
                f"{PREFIX}/{key}"
                for key in ("managed", "state", "error", "config-hash", "observed-generation")
            },
        )
        self.remove_empty_stanzas(essence)
        return essence


def selected(body, **_):
    annotations = effective_annotations(body)
    return (
        annotations.get(f"{PREFIX}/enabled", "false").strip().lower() != "false"
        or body.get("metadata", {}).get("annotations", {}).get(MANAGED) == "true"
    )


@kopf.on.startup()
def startup(settings, **_):
    global manager
    logging.getLogger().setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
    manager = KumaManager(Settings.from_env())
    settings.peering.standalone = True
    settings.scanning.disabled = True
    storage_prefix = "kopf.zalando.org" if PREFIX == "uptime-kuma.io" else f"kopf.{PREFIX}"
    settings.persistence.finalizer = f"{storage_prefix}/KopfFinalizerMarker"
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage(prefix=storage_prefix)
    settings.persistence.diffbase_storage = MonitorDiffBaseStorage(prefix=storage_prefix)
    settings.posting.level = logging.WARNING


@kopf.on.cleanup()
def shutdown(**_):
    if manager:
        manager.close()


@kopf.on.probe(id="kuma")
async def probe(**_):
    return {"configured": manager is not None, "outcomes": dict(outcomes)}


def handle(body, patch, logger, retry=0, deleting=False, **_):
    try:
        config = None if deleting else parse(body)
        # A durable marker keeps cleanup selected after annotations are removed.
        if config is not None and body["metadata"].get("annotations", {}).get(MANAGED) != "true":
            patch.metadata.annotations[MANAGED] = "true"
            return
        with manager.session() as api:
            result = reconcile(
                api,
                body,
                config,
                deleting=deleting,
                status_page=getattr(getattr(manager, "settings", None), "status_page", None),
            )
        outcomes[result] += 1
        if not deleting:
            annotations = patch.metadata.annotations
            annotations[f"{PREFIX}/state"] = result
            annotations[f"{PREFIX}/config-hash"] = hashlib.sha256(
                json.dumps(config, sort_keys=True).encode()
            ).hexdigest()
            annotations[f"{PREFIX}/observed-generation"] = str(body["metadata"].get("generation", 0))
            annotations[f"{PREFIX}/error"] = None
            if config is None:
                annotations[MANAGED] = None
    except InvalidConfig as exc:
        outcomes["invalid"] += 1
        if not deleting:
            patch.metadata.annotations[f"{PREFIX}/error"] = str(exc)
        logger.warning("Configuration/ownership rejected: %s", exc)
        # Retry also lets explicit legacy adoption recover without another edit.
        raise kopf.TemporaryError(str(exc), delay=300) from None
    except Exception:
        outcomes["retry"] += 1
        logger.warning("Kuma operation failed; retrying without changing desired state")
        raise kopf.TemporaryError("Kuma operation failed", delay=min(300, 5 * 2 ** min(retry, 6))) from None


def delete(body, patch, logger, **kwargs):
    return handle(body, patch, logger, deleting=True, **kwargs)


# Preserve existing handler IDs and default Kopf persistence/finalizer keys.
for plural in ("deployments", "statefulsets", "daemonsets"):
    for event in ("create", "update", "resume"):
        getattr(kopf.on, event)("apps", "v1", plural, id=f"{event}-{plural}", when=selected)(handle)
    kopf.on.delete("apps", "v1", plural, id=f"delete-{plural}", when=selected)(delete)
    kopf.on.timer(
        "apps",
        "v1",
        plural,
        id=f"resync-{plural}",
        when=selected,
        interval=resync_interval(),
        initial_delay=10,
    )(handle)
