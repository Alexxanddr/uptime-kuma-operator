# Changelog

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
