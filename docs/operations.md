# Operations and troubleshooting

## Health and visibility

The supplied deployment exposes Kopf health at `http://POD:8080/healthz`.
Startup, liveness, and readiness probes use this endpoint. The response includes
configuration availability and process-local reconcile outcome counters.

Process readiness does not prove Kuma authentication or endpoint health. Counters
reset on restart and are not a Prometheus exporter. Monitor operator warning
events/logs and the workload state/error annotations as well.

```sh
kubectl logs --namespace uptime-kuma-operator deployment/uptime-kuma-operator
kubectl get events --namespace YOUR_WORKLOAD_NAMESPACE --sort-by=.lastTimestamp
```

## Common failures

| Symptom | Investigation and recovery |
| --- | --- |
| No monitor appears | Confirm enabled/target annotations, inspect the error annotation, and check Kuma credentials/connectivity |
| Missing group or notification | Create the referenced object or correct its name; the controller retries without clearing current associations |
| Legacy ownership rejected | Verify the monitor belongs to this workload, then follow the explicit adoption procedure in [migration](migration.md) |
| Display name already in use | Choose a unique name; a name match alone does not authorize adoption |
| Multiple ownership markers | Resolve duplicate records manually after verifying ownership; automatic mutation is blocked |
| Connection/authentication errors | Check Socket.IO routing, credentials, TLS trust and timeout; restart after changing Secret values |
| Kuma edits are reverted | Managed fields are restored on resync; edit workload annotations to change desired state |
| Workload deletion is pending | Restore Kuma access so the finalizer can finish cleanup |

Retries for transient failures use delays of 5, 10, 20, 40, 80, 160, then 300
seconds. Validation/ownership failures retry at 300 seconds and can also recover
on a corrected resource event or periodic resync. Resync defaults to 300 seconds.

## Deletion and emergency recovery

Keep the operator and Kuma running until owned monitor cleanup completes. A
missing remote monitor is a successful no-op. A Kuma outage intentionally delays
deletion of managed resources through the finalizer; unmanaged workloads should
not depend on Kuma availability.

Removing a finalizer skips external cleanup. Use it only after identifying the
specific workload/monitor and accepting or repairing the orphaned external
state. Remove only the affected controller finalizer; never bulk-clear unrelated
finalizers. See [migration and rollback](migration.md) before downgrading.

## Credential and workload scope

The Kuma account can configure monitors. Protect its Secret and network access;
the operator has no per-monitor Kuma authorization boundary. Kubernetes RBAC can
restrict which workloads are watched and patched. Use one active instance per
watched scope/account: there is no distributed leader election.
