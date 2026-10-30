"""Run full validation with disposable Kind or an explicitly selected test cluster."""

import argparse
import datetime
import secrets
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kubeconfig",
        default=os.getenv("KUBECONFIG"),
        help="Use an authorized existing cluster instead of disposable Kind",
    )
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "artifacts" / "test-runs" / stamp
    output.mkdir(parents=True)
    python = str(ROOT / ".venv/bin/python")
    kubeconfig = (
        Path(args.kubeconfig).expanduser().resolve() if args.kubeconfig else output / "kind.kubeconfig"
    )
    env = {**os.environ, "KUBECONFIG": str(kubeconfig)}
    kind_name = "kumaops-check-" + secrets.token_hex(4) if not args.kubeconfig else None
    results = []

    def run(name, command, extra=None):
        print(f"RUN {name}", flush=True)
        log = output / (name + ".log")
        started = datetime.datetime.now(datetime.timezone.utc)
        with log.open("w") as stream:
            try:
                process = subprocess.run(
                    command, cwd=ROOT, env={**env, **(extra or {})}, stdout=stream, stderr=subprocess.STDOUT
                )
                code = process.returncode
            except OSError as exc:
                stream.write(str(exc) + "\n")
                code = 127
        result = {
            "name": name,
            "passed": code == 0,
            "exit_code": code,
            "seconds": (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds(),
            "log": str(log.relative_to(ROOT)),
        }
        results.append(result)
        (output / "results.json").write_text(json.dumps({"checks": results}, indent=2) + "\n")
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} ({result['seconds']:.1f}s)", flush=True)
        if code:
            print(log.read_text()[-5000:], flush=True)
        return code == 0

    run("lint", [python, "-m", "ruff", "check", "."])
    run("format", [python, "-m", "ruff", "format", "--check", "."])
    run(
        "suite-kuma-v2",
        [
            python,
            "-m",
            "pytest",
            "--cov",
            "--cov-report=xml",
            "--cov-report=html",
            "--cov-report=term-missing",
            f"--junitxml={output / 'pytest.xml'}",
        ],
        {"RUN_KUMA_INTEGRATION": "1", "KUMA_TEST_IMAGE": "louislam/uptime-kuma:2.0.2"},
    )
    run(
        "integration-kuma-v1",
        [python, "-m", "pytest", "tests/test_integration.py", "-v"],
        {"RUN_KUMA_INTEGRATION": "1", "KUMA_TEST_IMAGE": "louislam/uptime-kuma:1.23.16"},
    )
    run(
        "integration-kuma-v258",
        [python, "-m", "pytest", "tests/test_integration.py", "-v"],
        {"RUN_KUMA_INTEGRATION": "1", "KUMA_TEST_IMAGE": "louislam/uptime-kuma:2.5.8"},
    )
    run("manifest-schemas", [python, "scripts/validate_manifests.py"])
    run("python-build", [python, "-m", "build"])
    run(
        "dependency-audit",
        [python, "-m", "pip_audit", "-r", "requirements.txt", "--no-deps", "--disable-pip"],
    )
    run("dockerfile", ["docker", "build", "--check", "."])
    if run("container-build", ["docker", "build", "-t", "kumaops:test", "."]):
        run(
            "container-runtime",
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "kumaops:test",
                "python",
                "-c",
                "import os,operator,kuma_operator.handlers; "
                "assert os.getuid()==10001; assert operator.__eq__(1,1)",
            ],
        )
    cluster_ready = True
    if kind_name:
        cluster_ready = run(
            "kind-create",
            [
                "kind",
                "create",
                "cluster",
                "--name",
                kind_name,
                "--kubeconfig",
                str(kubeconfig),
                "--wait",
                "120s",
            ],
        )
        if kubeconfig.exists():
            kubeconfig.chmod(0o600)
    try:
        if cluster_ready:
            run("e2e-kubernetes", [python, "scripts/e2e.py", "--run"])
            run("cluster-cleanup", ["kubectl", "get", "namespaces", "-l", "kumaops-test", "-o", "json"])
            cleanup = results[-1]
            if cleanup["passed"]:
                inventory = json.loads((ROOT / cleanup["log"]).read_text())
                if inventory.get("items"):
                    cleanup["passed"] = False
                    cleanup["exit_code"] = 1
                    print("FAIL cluster-cleanup: test namespaces remain; see inventory", flush=True)
    finally:
        if kind_name:
            run("kind-delete", ["kind", "delete", "cluster", "--name", kind_name])
            kubeconfig.unlink(missing_ok=True)
    report = {"checks": results, "passed": all(r["passed"] for r in results)}
    coverage = ROOT / "coverage.xml"
    if coverage.exists():
        attrs = ET.parse(coverage).getroot().attrib
        report["coverage"] = {
            key: attrs[key] for key in ("lines-covered", "lines-valid", "branches-covered", "branches-valid")
        }
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Automated validation", "", f"UTC run: {stamp}", "", "| Check | Result |", "| --- | --- |"]
    lines += [f"| {r['name']} | {'PASS' if r['passed'] else 'FAIL'} |" for r in results]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Report: {output / 'report.md'}", flush=True)
    print("ALL CHECKS PASSED" if report["passed"] else "VALIDATION FAILED", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
