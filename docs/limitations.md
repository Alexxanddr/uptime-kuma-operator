# Compatibility and limitations

## Exercised combinations

The automated matrix uses CPython 3.11, the pinned client/framework resolution in
`requirements.txt`, and real Uptime Kuma **1.23.16**, **2.0.2**, **2.5.8**, and
**2.5.9** instances. E2E uses Kuma 2.5.9 and Kubernetes `apps/v1` workloads.
Manifest schemas are validated against Kubernetes 1.32.0; this is a schema
target, not a claim of a full Kubernetes version matrix. Consult actual run
reports for the environments exercised.

Other Kuma/Python/Kubernetes versions may work, but upstream compatibility claims
alone are not treated as test evidence. The runtime uses Kuma's Socket.IO API;
server protocol changes require client compatibility checks.

## Operational constraints

- One active process per watched scope/Kuma account; no distributed leader election.
  Keep a single replica and the supplied `Recreate` rollout strategy.
- Monitor discovery is O(N) per reconciliation. Large-scale behavior has not been
  benchmarked; benchmark before introducing caching or parallel remote writes.
- Ownership is stored in the monitor description. Removing it externally requires
  verified manual recovery rather than automatic adoption by name or URL.
- Legacy ownership lacks kind/UID and requires explicit adoption. Distinct workload
  kinds with identical namespace/name also need distinct display names.
- The Kuma account has configuration access; there is no per-monitor account
  authorization boundary. Protect credentials and scope access appropriately.
- Health indicates process availability, not guaranteed remote authentication.
  Counters are process-local and are not a Prometheus exporter.
- Username/password login is exposed; two-factor tokens and custom CA paths are
  not configurable through the current environment interface.
- E2E tests the source deployment separately from the final image's registry
  delivery. Multiarchitecture publication is configured in CI but must actually
  run before claiming a published artifact exists.

## Remaining engineering work

| Area | Next improvement |
| --- | --- |
| High availability | Add leader election only when a concrete HA requirement justifies it |
| Scale | Benchmark representative resource counts and Kuma latency |
| Observability | Add a Prometheus exporter or remote readiness policy when operationally required |
| Supply chain | Pin reviewed base-image digests and action commit SHAs with an update process |
| Image delivery | Exercise a built release image through a registry in E2E |
| Recovery | Extend network-partition and disaster rollback testing |

Forced process termination can bypass cleanup. Inspect only the unique test
resources identified by the run before repairing fixture state. There is no
claim of exhaustive fuzzing, scale certification, multi-process race detection,
or compatibility with untested versions.

Status page synchronization requires Kuma 2.5.8 or 2.5.9 and a single writer.
Explicit reserved group IDs avoid name adoption; group creation/deletion is administrative.
Manual hidden links cannot be safely reconstructed and block saves outside the
reserved membership contract. See [status pages](status-pages.md).
