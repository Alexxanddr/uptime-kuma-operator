# Status page synchronization

Status page synchronization is disabled by default. Monitor management remains
compatible with the existing Kuma version matrix; **status page writes require
Kuma 2.5.8 or 2.5.9**. Other server versions are rejected until their save/read
contract is tested. Use a dedicated page and a single writer for its contents.

## Explicit configuration

Create a status page and its empty public groups in Kuma before enabling the
feature. Record their numeric IDs; names are never used as ownership evidence.
Configure all three environment variables on the operator:

```sh
KUMA_STATUS_PAGE_SLUG=services
KUMA_STATUS_PAGE_ID=2
KUMA_STATUS_PAGE_GROUPS='{"102":9,"103":10}'
```

The JSON object maps **Kuma monitor-group IDs** to **public status-page group
IDs** on the selected page. IDs must be unique positive destinations. Source
`"0"` explicitly maps ungrouped monitors. A missing destination blocks saves;
the controller never recreates a page/group or adopts one by matching its name.
Environment changes require an operator restart.

Add this annotation only to workloads approved for publication:

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/status-page: "true"
```

Normal metadata/template precedence applies. Omitting or setting `status-page`
to `false` removes that monitor's associations from reserved groups. This is
independent of disabling monitoring, which deletes the exact UID-owned monitor.
For an existing inventory, `KUMA_STATUS_PAGE_MONITORS='[201,202]'` explicitly
opts in existing monitor IDs without changing workload pod templates. UID
ownership is still required; a recreated monitor with a different ID is not
implicitly approved. An explicit workload `status-page: "false"` overrides this
allowlist; removing the annotation falls back to the allowlist. New monitors
can use the annotation. Removing an ID from the allowlist cleans its reserved
associations on reconciliation. This avoids restarting Helm workloads merely to
add a publication annotation.

No additional Kubernetes RBAC, registry, CRD or dependency is required.

## Ownership and manual configuration

The configured destination groups reserve their titles and the memberships/link
settings of UID-marked (`k8s:v2:`) monitors for this controller. Reserve them for
one controller only. Association ownership is this explicit administrative
reservation, not an extra field stored in Kuma. Manual associations of these
monitors in reserved groups are therefore inside the controller's scope. Do not
add custom URLs, even hidden ones, to these reserved associations.

Other group IDs, their ordering, manual memberships and page configuration are
preserved. Manual associations with visible links can be round-tripped. Kuma's
public read hides custom URLs when `sendUrl=false`; if an association outside
the reserved ownership contract has a hidden link, the operator **refuses the
whole save**, because it cannot prove that the URL would be preserved. This also
blocks finalizer cleanup until the conflict is resolved. Put manual content on a
separate page when it cannot satisfy this readback contract.

The controller uses the authenticated configuration unchanged, including
`autoRefreshInterval`, and a cache-bypassed public membership read. It bypasses
the client's lossy `save_status_page` builder. It checks page identity, rereads
before saving and verifies the saved configuration and membership afterward.
Unknown manual page configuration is carried through, not rebuilt from defaults.

**Kuma has no conditional save/revision API and no whole-save transaction.**
The second read detects many concurrent changes, but cannot prevent an admin or
another controller writing between that check and the save. Require one writer;
this is not a promise of safe simultaneous editing. An incomplete save or lost
acknowledgement is retried from a new read without creating new groups.

## Hierarchy, order and lifecycle

Kuma monitor groups and public page groups are independent structures. A public
group contains an ordered monitor list, not nested public groups. Each mapped
source group becomes a flat public group named by its full path, for example
`Platform / Databases`. Identical paths do not merge IDs. Cycles and broken
ancestor references are rejected. An unmapped parent prevents publication.

Existing group order and monitor positions are retained. New and moved monitors
are appended in serialized reconciliation order; subsequent passes do not sort
or rewrite them. Reordering existing entries manually is preserved. Titles of
mapped groups follow source renames. Missing source groups leave their existing
containers in place; groups are never automatically deleted.

Create, rename and type changes retain normal monitor UID ownership. A group
move removes the monitor from reserved destinations and inserts it at the new
destination. Suspended/inherited-inactive monitors are removed from the page;
resuming them republishes on reconciliation. Cleanup precedes monitor deletion.
Removing opt-in leaves the monitor and history intact. Deletion of an unrelated
monitor never authorizes publication or cleanup by display name.

Keep former destination IDs in the map until all affected workloads have been
reconciled or opted out. Before changing page identity or disabling this feature,
opt out and verify cleanup on the old page. The controller does not scan former
pages or abandoned destination IDs. Changing a monitor ownership description
externally requires explicit recovery, as with ordinary monitor management.

## Publication and validation

New managed associations always use `sendUrl=false` with no custom URL. Names,
group paths, IDs, monitor types, uptime, timestamps and latency still reveal
service inventory and operational state. Existing page-wide tag/certificate,
incident, domain and appearance settings remain unchanged. Treat a status page
as public unless access is independently enforced at the network/proxy layer;
`published=false` is not an authorization boundary.

Test changes using the disposable fixture in `tests/test_integration.py`, then
run `make test` with an explicitly authorized cluster or its default disposable
Kind cluster. The runner includes real Kuma 2.5.8 and 2.5.9 status-page tests
and Kubernetes E2E publication/opt-out/finalizer tests for all three workload
kinds. Unit tests
cover hierarchy, order, manual preservation/refusal, idempotence, partial saves,
lost acknowledgements and concurrent-change detection. No test accepts a
production Kuma URL.
