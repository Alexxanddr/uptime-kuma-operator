# Installation

## Requirements

- Kubernetes providing the `apps/v1` Deployment, StatefulSet, and DaemonSet APIs.
- A reachable Uptime Kuma instance and an account with monitor management access.
- Permission to create the supplied namespace, ServiceAccount, and RBAC resources.
- An operator image built from the desired source revision or a verified release.

See [compatibility](limitations.md) for the combinations exercised by tests.
The account authenticates to Socket.IO with a username and password; a Kuma
Prometheus API key does not authenticate this integration. Two-factor login and
custom CA configuration are not exposed by the current environment interface.

Select the cluster using your normal kubeconfig workflow. Every command below
uses the cluster you selected; no personal kubeconfig path is embedded in the
manifests. The default installation watches all namespaces.

## Image

Use a verified release tag or digest when available and export it as `IMAGE`.
For a checkout that has not
been released yet:

```sh
export IMAGE=your-registry.example.com/uptime-kuma-operator:dev
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

The registry must be reachable by your nodes. Configure an imagePullSecret if
required. Build for your nodes' architecture, or use Docker Buildx to publish
both `linux/amd64` and `linux/arm64`. The `latest` reference in the compatibility
manifest is mutable and may describe an earlier release.

## RBAC and credentials

```sh
kubectl apply -f kubernetes/rbac.yaml
```

This creates namespace `uptime-kuma-operator`, a ServiceAccount, and cluster-wide
get/list/watch/patch access to the three workload kinds, plus event creation.
The operator does not need Kubernetes Secret read permission: the kubelet injects
its credentials through Secret references.

Create an Opaque Secret in that namespace with these keys:

| Key | Value |
| --- | --- |
| `url` | Kuma base URL, such as `https://kuma.example.com` |
| `user` | Kuma username |
| `pass` | Kuma password |

Use your secret manager or populate a local copy of
[`kubernetes/secret.yaml`](../kubernetes/secret.yaml). Keep that copy outside the
repository or in an ignored local file. The checked-in values are placeholders.

## Deploy and verify

```sh
kubectl set image --local -f kubernetes/operator.yaml \
  operator="$IMAGE" -o yaml | kubectl apply -f -
kubectl rollout status deployment/uptime-kuma-operator \
  --namespace uptime-kuma-operator --timeout=120s
kubectl logs --namespace uptime-kuma-operator deployment/uptime-kuma-operator
```

A ready Pod proves process availability; verify actual reconciliation too:

```sh
kubectl apply -f kubernetes/example-deployment.yaml
kubectl wait --for=jsonpath='{.metadata.annotations.uptime-kuma\.io/state}' \
  deployment/sample-app --timeout=120s
kubectl get deployment sample-app \
  -o jsonpath='{.metadata.annotations.uptime-kuma\.io/state}'
```

The example uses zero replicas and monitors `https://example.com`; it demonstrates
monitor lifecycle without launching an application. Wait for state `created` or
`unchanged` and confirm the monitor in Kuma. If reconciliation fails, inspect the
`uptime-kuma.io/error` annotation and operator logs.

Remove the demonstration with `kubectl delete -f
kubernetes/example-deployment.yaml`. That deletion also exercises monitor cleanup.

## Scope and production settings

Run one replica and retain the `Recreate` strategy so a rollout does not overlap
uncoordinated instances. The container runs as UID 10001, drops capabilities,
uses a read-only root filesystem plus `/tmp`, and has resource bounds and probes.
Adjust resources after measuring your workload count and Kuma latency.

For a single namespace, replace `--all-namespaces` with
`--namespace=YOUR_NAMESPACE`. Replace the ClusterRole and ClusterRoleBinding with
a Role and RoleBinding in that namespace granting the same workload/event verbs;
if the ServiceAccount lives elsewhere, reference its namespace in the binding.
Dynamic namespace/CRD discovery is disabled, so wildcard namespace discovery is
not supported. Check your rendered manifests before applying the change.

Prefer HTTPS for network boundaries. Restrict network access to the Kubernetes
API and Kuma endpoint, rotate account credentials, and restart the operator after
Secret changes because environment variables are loaded at process startup.

## Removal

Disable managed monitoring or delete the managed workloads first, while the
operator and Kuma are still available. Confirm external cleanup before removing
the operator deployment, Secret, and RBAC. Removing the controller first can
leave finalizers or orphan monitors; see [operations](operations.md).
