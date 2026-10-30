# Uptime Kuma Kubernetes Operator

Manage Uptime Kuma monitors from annotations on Kubernetes **Deployments**,
**StatefulSets**, and **DaemonSets**. The operator uses Python, Kopf, and the
Kuma Socket.IO API; no CustomResourceDefinitions are required.

- HTTP, TCP, ping, and DNS monitors.
- Stable ownership across renames, with protection against editing another resource's monitor.
- Existing notification and parent-group associations.
- Periodic drift correction, retry handling, and deletion cleanup.
- Automated tests against real Kuma v1/v2 instances and Kubernetes.

## Quick start

You need Kubernetes credentials with permission to install the supplied RBAC,
an existing Kuma instance reachable from the cluster, and a Kuma account that
can manage monitors. Run **one active operator per watched scope and Kuma account**.

The documentation follows this source revision. A previously published `latest`
image may not include these changes; releases use explicit version tags.
To try this checkout, build and push it to a
registry your cluster can pull from:

```sh
git clone https://github.com/Alexxanddr/uptime-kuma-operator.git
cd uptime-kuma-operator
export IMAGE=your-registry.example.com/uptime-kuma-operator:dev
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

Select your intended cluster before applying anything. Then install RBAC:

```sh
kubectl apply -f kubernetes/rbac.yaml
```

Create Secret `uptime-kuma-creds` in namespace `uptime-kuma-operator` using your
secret manager, with keys `url`, `user`, and `pass`. The
[Secret template](kubernetes/secret.yaml) shows the expected shape; replace its
placeholders locally and keep real credentials out of Git.

Set the image reference in a rendered deployment and install it:

```sh
kubectl set image --local -f kubernetes/operator.yaml \
  operator="$IMAGE" -o yaml | kubectl apply -f -
```

See the [installation guide](docs/installation.md) for verification, namespace
scoping, prerequisites, and production settings. Existing users should read the
[migration guide](docs/migration.md) before replacing their operator.

## Monitor a workload

Add annotations to workload metadata or Pod template metadata. Workload metadata
wins when both specify the same key:

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/type: "http"
    uptime-kuma.io/url: "https://example.com"
    uptime-kuma.io/name: "Example service"
```

The operator creates the monitor, applies later changes, and removes its owned
monitor when monitoring is disabled or the workload is deleted. Invalid input
preserves the last remote configuration and reports an error.

[Examples](docs/examples.md) cover all monitor types.
The [configuration reference](docs/configuration.md) lists every annotation,
environment variable, and generated state field.

## Development

Install [uv](https://docs.astral.sh/uv/), Docker with BuildKit, kubectl, Kind, and
Make, then run:

```sh
make test
```

This prepares Python 3.11, runs the complete validation suite against disposable
Kuma instances, creates a temporary Kind cluster, runs Kubernetes E2E, and verifies
cleanup. It does not use your current Kubernetes context by default. Logs,
JUnit results, coverage, and JSON/Markdown reports are generated automatically.
See [testing](docs/testing.md) for using an explicitly authorized existing cluster
and for smaller diagnostic checks.

## Documentation

Start at the [documentation index](docs/README.md). It includes
[architecture](docs/architecture.md), [operations and troubleshooting](docs/operations.md),
[compatibility and limitations](docs/limitations.md), and [migration](docs/migration.md).

Contributions are welcome: read [CONTRIBUTING.md](CONTRIBUTING.md).
Report vulnerabilities using [SECURITY.md](SECURITY.md).
This project is licensed under the [MIT License](LICENSE).
