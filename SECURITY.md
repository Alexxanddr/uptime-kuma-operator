# Security policy

## Reporting a vulnerability

Use the repository's
[private vulnerability reporting page](https://github.com/Alexxanddr/uptime-kuma-operator/security/advisories/new)
when available. Include the affected revision, impact, minimal reproduction, and
relevant sanitized logs. Keep credentials, access tokens, kubeconfigs, and
production data out of reports. If private reporting is unavailable, open a
public issue requesting a private contact channel without disclosing exploit details.

Compatibility and test scope are documented in [limitations](docs/limitations.md).
There is no published response-time guarantee or separate long-term-support policy.

## Deployment boundaries

Use a dedicated Kuma account/scope, protect Kubernetes Secrets, and restrict
network access. Prefer TLS across trust boundaries. The operator needs workload
patch access to maintain annotations/finalizers, but does not need Kubernetes
Secret read permission. Run one active instance per watched scope/account.

The container runs without root privileges, with dropped capabilities and a
read-only root filesystem in the supplied deployment. Dependency audit runs in
CI; container scans gate releases and run periodically. A clean scan is not a
claim that all code or dependencies are vulnerability-free.

## Tests

Use disposable Kuma and isolated Kubernetes resources. The default full runner
creates Kind rather than using the current context. Setting `KUBECONFIG` opts into
testing on that selected existing cluster; use it only with appropriate authorization.
Generated credentials, kubeconfigs, and test reports belong in ignored artifacts,
not source control.
