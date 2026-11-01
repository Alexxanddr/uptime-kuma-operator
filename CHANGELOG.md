# Changelog

## 1.2.1 — 2026-11-01

- Limit synchronous handlers to one worker because all Kuma transactions share
  one serialized session; this avoids memory growth during cluster-wide resumes.
- Support status-page synchronization on Kuma 2.5.9 after real integration and
  Kubernetes E2E verification.

## 1.2.0 — 2026-10-30

- Add disabled-by-default status page opt-in, explicit page/group ID mappings,
  flat full-path titles, preserved ordering and lifecycle cleanup on Kuma 2.5.8.
- Preserve page configuration including refresh intervals; reject manual links
  that cannot be safely round-tripped and detect observed concurrent edits.
- Test real status page saves, recovery and all workload kinds in isolated E2E.
- Require one writer; do not automatically create, adopt or delete public groups.

## 1.1.2 — 2026-10-30

- Exclude controller-generated state annotations from desired-state change
  detection, preventing status/generation feedback loops on Deployments.
- Preserve change detection for user annotations while retaining the existing
  framework storage keys and state interface.

## 1.1.1 — 2026-10-30

- Run health probes asynchronously so reconciliation worker saturation cannot
  cause false liveness failures during large initial syncs.
- Add a deterministic worker-starvation regression and a 64-workload E2E health,
  reconciliation, and cleanup test.

## 1.1.0 — 2026-10-30

### Reliability and safety

- Separate invalid input from disabled monitoring to prevent destructive cleanup.
- Identify monitors by workload kind, namespace, name, and UID; reject ownership
  and display-name conflicts instead of claiming foreign monitors.
- Require explicit verified adoption of legacy ownership markers.
- Serialize remote transactions and rebuild authenticated sessions after failures.
- Resolve groups/notifications before mutation, skip unchanged edits, and recover
  drift through periodic reconciliation.
- Persist managed state, expose configuration errors, and scope deletion finalizers.

### Compatibility

- Preserve the existing annotation API, top-level precedence, supported workload
  kinds, HTTP-code aliases, and `kopf run operator.py` entrypoint.
- Preserve upstream TCP/ping aliases, target defaults, host/port formats, and DNS resolver options.
- Change monitor types in place rather than deleting/recreating their IDs.
- Validate numeric limits and preserve exact HTTP code ranges.

Read [migration](docs/migration.md) before upgrading, especially for legacy
ownership adoption and rollback limitations.

### Development and operations

- Add behavioral unit/reconciliation tests, real Kuma v1/v2 integration tests,
  Kubernetes E2E, coverage reports, and automatic validation.
- Default public validation to disposable Kind; support explicitly selected test clusters.
- Harden the container/deployment and reduce RBAC permissions.
- Add release checks, multiarchitecture image publication configuration, and
  focused English installation, architecture, operations, and contributor guides.
- Remove unused Node preview scaffolding and keep generated run evidence out of source control.
