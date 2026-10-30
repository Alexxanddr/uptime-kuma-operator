from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_deployment_security_and_secret_references():
    deployment = yaml.safe_load((ROOT / "kubernetes/operator.yaml").read_text())
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    spec = deployment["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsNonRoot"]
    c = spec["containers"][0]
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert c["securityContext"]["readOnlyRootFilesystem"]
    assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
    for probe in ["readinessProbe", "livenessProbe", "startupProbe"]:
        assert c[probe]["httpGet"]["path"] == "/healthz"
    for key in ["KUMA_URL", "KUMA_USER", "KUMA_PASS"]:
        assert (
            next(e for e in c["env"] if e["name"] == key)["valueFrom"]["secretKeyRef"]["name"]
            == "uptime-kuma-creds"
        )


def test_rbac_has_no_secret_or_workload_update_access():
    docs = list(yaml.safe_load_all((ROOT / "kubernetes/rbac.yaml").read_text()))
    rules = next(d for d in docs if d["kind"] == "ClusterRole")["rules"]
    assert all("secrets" not in r["resources"] for r in rules)
    workload = next(r for r in rules if r["apiGroups"] == ["apps"])
    assert set(workload["verbs"]) == {"get", "list", "watch", "patch"}
    assert not any("/status" in r for r in workload["resources"])


def test_python_startup_does_not_shadow_stdlib():
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import operator; import collections; assert operator.eq(1,1); assert operator.__eq__(1,1)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
