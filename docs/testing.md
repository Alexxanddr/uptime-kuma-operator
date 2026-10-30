# Testing

## Full automatic validation

Requirements: a POSIX shell (Linux, macOS, or WSL), Make, uv, Docker with BuildKit,
kubectl, and Kind. Docker must be running. From the repository root:

```sh
make test
```

The runner prepares Python 3.11 and pinned dependencies, then runs:

1. Ruff lint and formatting.
2. The complete Python suite, including real Kuma 2.0.2 integration and coverage.
3. Real Kuma 1.23.16 integration.
4. Strict Kubernetes manifest schema validation.
5. Python wheel/sdist build and runtime dependency audit.
6. Dockerfile validation, container build, and a non-root/read-only runtime check.
7. Operator E2E on a newly created disposable Kind cluster.
8. Namespace cleanup verification and Kind deletion.

The runner does not select `~/.kube/config` or the current context implicitly.
Without an explicit `KUBECONFIG`, it writes a temporary kubeconfig for its own Kind
cluster. It removes that credential file and cluster after validation. A failed
check or cleanup produces a nonzero exit status and a failed report.

### Authorized existing cluster

To use a cluster where you have permission to create/delete isolated test
resources, select its kubeconfig explicitly:

```sh
KUBECONFIG=/path/to/test-cluster.kubeconfig make test
```

The same full suite runs, but Kind is not created or deleted. E2E creates a unique
labeled namespace, disposable Kuma, namespaced RBAC, and a test operator. It
watches only that namespace and uses a separate annotation prefix and framework
persistence/finalizers. Existing workloads and production Kuma data are outside
the test scope.

The E2E script can also be run independently:

```sh
KUBECONFIG=/path/to/test-cluster.kubeconfig \
  .venv/bin/python scripts/e2e.py --run
```

It rejects missing explicit cluster selection. Successful runs wait for actual
namespace disappearance before reporting success. Cleanup runs after failures;
a forced kill or loss of API access can still require inspecting the isolated
resources identified in the logs. It never bulk-removes another controller's
finalizers or deletes an unrelated namespace.

### Artifacts

Each full run writes `artifacts/test-runs/<UTC timestamp>/` containing:

- `report.md` and `results.json`: aggregate pass/fail, durations, and log paths.
- `pytest.xml`: JUnit test results.
- Per-check logs, including Kubernetes E2E and cleanup inventory.

Coverage is generated at `coverage.xml` and `htmlcov/index.html`. Artifacts,
credentials, and build output are ignored by Git. Reports from actual runs are
the evidence; committed documentation does not claim a CI run happened.

## Diagnostic checks

For a smaller local loop, prepare dependencies and run unit/reconciliation tests:

```sh
uv venv --python 3.11
uv pip sync --python .venv/bin/python requirements-dev.txt
.venv/bin/python -m pytest
```

Default pytest skips the six opt-in Docker integration cases. Run them against
a disposable Kuma instance with:

```sh
RUN_KUMA_INTEGRATION=1 .venv/bin/python -m pytest tests/test_integration.py -v
RUN_KUMA_INTEGRATION=1 KUMA_TEST_IMAGE=louislam/uptime-kuma:1.23.16 \
  .venv/bin/python -m pytest tests/test_integration.py -v
```

The fixture accepts an image selector, not an external Kuma URL. It generates
credentials, binds loopback, bootstraps SQLite, waits for a read-only Socket.IO
readiness RPC, and removes its container/volume in a finally block.

Other checks:

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python scripts/validate_manifests.py
.venv/bin/python -m build
.venv/bin/pip-audit -r requirements.txt --no-deps --disable-pip
docker build --check .
```

## Coverage matrix

| Behavior | Tests |
| --- | --- |
| Annotation precedence, defaults, validation, HTTP code ranges, legacy formats | `tests/test_config.py` |
| Create/update/rename/no-op/disable/delete and external drift | `tests/test_reconciler.py`, `scripts/e2e.py` |
| UID/kind/namespace/name collisions, safe legacy adoption | `tests/test_reconciler.py` |
| References resolved before mutation; preserve unrelated settings | `tests/test_reconciler.py`, `tests/test_integration.py` |
| Lost create/delete acknowledgements and outage recovery | `tests/test_reconciler.py` |
| Session serialization, reauthentication, bounded retry, state and cleanup | `tests/test_client_handlers.py` |
| Actual HTTP/TCP/ping/DNS lifecycle, groups and notifications | `tests/test_integration.py` on both Kuma versions |
| Actual API watches, finalizers, invalid configuration, rename, drift and cleanup | `scripts/e2e.py` on all three workload kinds |
| Deployment/RBAC/Secret contracts and compatibility entrypoint | `tests/test_manifests.py` |
| Documentation links, runnable examples, portable public entrypoints | `tests/test_repository.py` |
| Kubernetes OpenAPI schemas | `scripts/validate_manifests.py` |

Local package coverage measures lines and branches. Cluster execution occurs in
another process/container and is not merged into that report. Thread tests use
synchronization events to verify serialization; they are not a race detector for
Python extensions. No arbitrary coverage threshold replaces behavioral tests.

## E2E scope and CI

E2E deploys locked sources in a stock Python image, avoiding publication of an
unreviewed test image. The production image is built and smoke-tested separately;
E2E does not validate delivery of that image through your registry. Test workloads
use zero replicas or an impossible DaemonSet node selector.

GitHub Actions runs checks and E2E on every pull request and main push, using a
Kind cluster owned by its runner. It does not require access to a maintainer's
cluster. Container vulnerability scans run periodically and gate releases;
release validation also runs both Kuma versions and E2E before publication.
