# Contributing

Thank you for contributing. Keep changes small, preserve existing annotation
contracts, and test the behavior that changes.

## Development setup

Clone the repository and install uv, Docker with BuildKit, kubectl, Kind, and
Make. The automatic entrypoint prepares Python 3.11 and pinned dependencies:

```sh
make test
```

By default, validation uses disposable Docker Kuma instances and a new Kind
cluster. An existing cluster is used only when explicitly selected with
`KUBECONFIG`; use a cluster where isolated E2E resource creation is authorized.
See [testing](docs/testing.md) for details, reports, and faster diagnostic checks.

## Change conventions

- Write documentation, release notes, and agent instructions in English.
- Keep public instructions independent of personal directories or cluster names.
- Add behavioral tests for new features, failure recovery, and compatibility fixes.
- Keep parsing/validation free of remote mutations and preserve ownership safety.
- Update the [configuration reference](docs/configuration.md) and examples when
  changing annotations or environment settings.
- Document user-visible changes in [CHANGELOG.md](CHANGELOG.md), including any
  migration requirements. Discuss breaking changes before implementing them.
- Never commit credentials, generated kubeconfigs, test logs, or build/coverage output.

The runtime package is `kuma_operator/`. `operator.py` remains a compatibility
entrypoint; read [architecture](docs/architecture.md) before changing lifecycle
or persistence behavior. Test provisioning/reporting lives in `scripts/`.

## Pull requests

Explain the problem, resulting behavior, compatibility impact, and checks actually
executed. The CI runs lint, unit/integration tests, schema/build/audit checks, and
Kubernetes E2E on its own disposable Kind cluster. A workflow definition alone is
not evidence that its checks ran.

Dependency updates start in `requirements.in` and `requirements-dev.in`:

```sh
uv pip compile --upgrade requirements.in -o requirements.txt
uv pip compile requirements-dev.in -o requirements-dev.txt
make test
```

Review the resolved diff and exercise both Kuma versions. For security reports,
follow [SECURITY.md](SECURITY.md) instead of posting exploit details in a public issue.
