# Migration and compatibility

No CRD or conversion webhook is required. Preserve the workload annotations,
KUMA_URL/KUMA_USER/KUMA_PASS Secret keys and `kopf run ... operator.py` entrypoint.
Top-level annotations override template annotations individually. The TCP/ping aliases, target defaults, host/port formats, and DNS resolver
options from the upstream TCP-support release are preserved.

## Before upgrading

1. Export a backup of the existing Kuma database using its supported procedure.
2. Inventory workload kind/namespace/name/UID and current monitor descriptions.
3. Check duplicate display names and collisions between workload kinds.
4. Stop the old operator before starting the replacement. Use one replica and
   Recreate. If deployment manifests live in a separate GitOps repository, update
   that configuration explicitly as part of the reviewed rollout.
5. Choose a tested image digest/version. No image is published by local tests.

## Legacy monitor adoption

Legacy ownership is incomplete, so migration is explicit. After verifying the
monitor belongs to the workload, add to workload metadata:

```yaml
uptime-kuma.io/adopt-legacy: "true"
```

A single exact `k8s:namespace/name` description can then be upgraded in place;
monitor ID is preserved. No adoption by custom/default display name or URL is
performed. An untagged record must be manually assigned the exact verified legacy
marker before opting in. Remove adopt-legacy after successful migration. Audit
and resolve any duplicate markers or kind/name collisions before adoption.

Already disabled workloads with old monitors require explicit inventory and
cleanup; the new controller never guesses destructive ownership. Old Kopf
finalizers may already exist on unmanaged workloads. With the replacement
running, verify they are released when no deletion handler applies; do not
bulk-remove finalizers during migration.

## Intentional behavior corrections

- Invalid configuration preserves the last remote state and reports an error.
- Unsupported types and malformed status codes are rejected.
- Status-code ranges are exact rather than widened.
- interval must be 20–86400 seconds; retries 0–1000; TCP port 1–65535.
- Missing/ambiguous groups or notifications prevent mutation and retry.
- Existing foreign/ambiguous monitors are not adopted or deleted.
- An outage delays managed-resource cleanup until Kuma recovers.

## Rollback

Back up manifests and annotations before rollout. Do not restart the old operator
against migrated v2 markers without a reviewed reverse migration: its prefix/name
fallback cleanup is unsafe. Prefer rolling forward with a fix; when rollback is
necessary, stop all operator instances, restore the Kuma backup and matching
workload/framework annotations, then restart the known previous version.

Emergency finalizer removal skips external cleanup and can orphan a monitor.
Inspect only the affected resource and repair external state before removing its
specific finalizer. Never remove all finalizers or all monitor descriptions.
