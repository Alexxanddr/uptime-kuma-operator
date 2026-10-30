# Configuration reference

## Annotation precedence

Use `uptime-kuma.io/` annotations on workload metadata or
`spec.template.metadata.annotations`. Workload metadata overrides the template
for each individual key. Kubernetes annotation values must be strings.

| Suffix | Meaning | Default |
| --- | --- | --- |
| `enabled` | `true` enables monitoring; `false` or removal disables it | `false` |
| `name` | Display name; creation/rename cannot claim another monitor's name | `k8s-{namespace}-{name}` |
| `type` | `http`, `tcp`, `ping`, or `dns`; TCP aliases: `port`, `tcp-port`, `tcpport`; ping alias: `icmp` | `http` |
| `url` | HTTP(S) URL; legacy non-HTTP hostname/port fallback | In-cluster HTTP URL fallback |
| `hostname` | Hostname/IP for TCP, ping, DNS; preferred over legacy `url` | `{name}.{namespace}.svc.cluster.local` |
| `port` | TCP/DNS port, 1–65535 | TCP target port or `80`; DNS `53` |
| `host` | Compatibility alias for `hostname` | None |
| `dns-server` | DNS resolver; `dns-resolve-server` is an alias | `1.1.1.1` |
| `interval` | Monitor check interval, 20–86400 seconds | `60` |
| `retries` | Monitor check retries, 0–1000; separate from controller retry policy | `3` |
| `status-codes` | Accepted HTTP codes/ranges, CSV or JSON array | `200-299` |
| `notifications` | Comma-separated existing notification names | None |
| `group` | Existing parent group name or numeric ID | None |
| `adopt-legacy` | Explicit opt-in to claim a verified exact legacy marker | `false` |

Boolean and type values accept case normalization. Invalid enabled values,
unknown types, malformed targets, and invalid numeric settings report
configuration errors without mutating Kuma.

HTTP status code aliases are checked in order: `status-codes`,
`accepted-statuscodes`, `accepted-status-codes`, `statuscodes`. The first provided
value wins. Duplicates are removed and partial ranges retain their exact meaning:
`201-250` never expands to the entire `200-299` range. The parser accepts code
values from 100 through 999 and rejects malformed or reversed ranges.

Notifications and groups must already exist and resolve unambiguously. A missing
reference prevents all mutations for that attempt; the operator retries so the
resource can recover after the referenced object is created.

### Target defaults and upstream compatibility

When no target is supplied, the in-cluster hostname defaults to
`{name}.{namespace}.svc.cluster.local`; HTTP prefixes it with `http://`.
Non-HTTP targets accept a hostname, `host:port`, or a scheme-qualified target,
including a port in `hostname`. Explicit `port` wins over the embedded port.
The legacy unprefixed `port` annotation remains a TCP fallback. DNS sends an
A-record query using the configured resolver and port.

### Legacy non-HTTP targets

`type: "port"` plus `url: "redis.database.svc:6379"` remains supported. Prefer
`type: "tcp"`, `hostname: "redis.database.svc"`, and `port: "6379"` for new
resources. For ping/DNS, prefer `hostname` over the legacy `url` field. See
[migration](migration.md) for ownership changes.

## Environment

| Variable | Meaning | Default |
| --- | --- | --- |
| `KUMA_URL` | Kuma HTTP(S) base URL | Required |
| `KUMA_USER` | Kuma account username | Required |
| `KUMA_PASS` | Kuma account password | Required |
| `KUMA_TIMEOUT` | Per-client-operation timeout in seconds, greater than 0 and at most 300 | `10` |
| `KUMA_RESYNC_INTERVAL` | Drift reconciliation interval, 1–86400 seconds | `300` |
| `KUMA_ANNOTATION_PREFIX` | Application annotation prefix; a custom prefix also isolates framework persistence | `uptime-kuma.io` |
| `LOG_LEVEL` | Root logging level | `INFO` |

Credentials are required at startup. Environment changes require a process
restart. Socket.IO transport authentication is managed by the client; automatic
unauthenticated reconnect is disabled in favor of rebuilding a logged-in session.

## Generated state

The following suffixes are reserved under the configured annotation prefix:

| Suffix | Meaning |
| --- | --- |
| `managed` | Durable cleanup selection marker across annotation removal/restarts |
| `state` | Last successful reconcile outcome: created, updated, unchanged, deleted, or absent |
| `error` | Sanitized validation/ownership error; cleared after successful reconciliation |
| `config-hash` | SHA-256 of normalized desired monitor configuration |
| `observed-generation` | Last successfully observed workload generation |

Generation alone is insufficient because annotation edits need not increment it;
the effective configuration hash also records desired-state changes. A previous
successful state can remain while a later attempt fails; inspect errors and
warning events together. Transient integration failures are reported through
logs/events and retry state, not arbitrary remote error text in the error field.

Kopf additionally stores progress and last-handled configuration in its own
annotations. Treat both application and framework state as controller-owned.
