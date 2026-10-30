# Repository instructions

## Documentation

Write all documentation in English, including README files, guides, release
notes, and agent instructions. Keep public documentation portable: use generic
cluster paths and examples, and keep personal environment settings in ignored
local files. Read `AGENTS.local.md`, if present, before running cluster operations.

## Autonomous validation

After changing code, dependencies, manifests, CI, or test tooling, run `make test`
yourself before declaring the work complete. This entrypoint prepares the
environment and runs the full validation suite, including real Kuma integrations
and Kubernetes E2E. Documentation-only changes need relevant documentation checks.

Carry forward explicit authorization to use a test cluster; do not ask again or
hand execution back to the user as a list of commands. For an authorized existing
cluster, pass its kubeconfig through `KUBECONFIG`. Otherwise the public runner
creates its own disposable Kind cluster. Never silently select a shared cluster.

Use isolated namespaces and Kuma instances. Verify cleanup before reporting
success. Fix failures and rerun affected checks when possible; report concrete
blockers and checks not executed. Cluster test authorization does not authorize
production rollouts or changes to existing workloads or production Kuma data.

Report actual execution results and the generated report path. Distinguish
configured CI workflows from runs that actually happened. See
[docs/testing.md](docs/testing.md) for the test contract and
[CONTRIBUTING.md](CONTRIBUTING.md) for repository conventions.
