"""Public documentation and entrypoint contracts that users rely on."""

import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md"))
DOCUMENTS = [p for p in DOCUMENTS if p.name != "AGENTS.local.md"]


def test_documentation_local_links_resolve():
    missing = []
    for document in DOCUMENTS:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text()):
            target = target.split()[0]
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            if not (document.parent / unquote(parsed.path)).exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert not missing, "\n".join(missing)


def test_example_workload_has_valid_monitor_configuration():
    from kuma_operator.config import parse

    body = yaml.safe_load((ROOT / "kubernetes/example-deployment.yaml").read_text())
    body["metadata"].update(namespace="example", uid="example-uid")
    config = parse(body)
    assert config["type"] == "http"
    assert config["url"] == "https://example.com"
    assert config["notifications"] == []
    assert config["group"] == ""
    assert body["spec"]["replicas"] == 0


def test_e2e_rejects_implicit_cluster_selection():
    env = {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}
    result = subprocess.run(
        [sys.executable, "scripts/e2e.py", "--run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "no cluster is selected implicitly" in result.stderr


def test_runner_creates_and_deletes_kind_when_no_cluster_is_selected(tmp_path):
    # Execute the real runner with an isolated command harness. No real tools or
    # cluster are used here; the behavioral contract is cluster selection/cleanup.
    program = textwrap.dedent("""
        import importlib.util, os, pathlib, subprocess, sys
        spec=importlib.util.spec_from_file_location('check', 'scripts/check.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.ROOT=pathlib.Path(sys.argv[1])
        sys.argv=['check.py']
        os.environ.pop('KUBECONFIG', None)
        commands=[]
        def command(args, **kwargs):
            commands.append(args)
            if args[:3]==['kubectl','get','namespaces']:
                kwargs['stdout'].write('{"items": []}')
            return subprocess.CompletedProcess(args, 0)
        module.subprocess.run=command
        assert module.main()==0
        create=next(c for c in commands if c[:3]==['kind','create','cluster'])
        delete=next(c for c in commands if c[:3]==['kind','delete','cluster'])
        assert create[create.index('--name')+1]==delete[delete.index('--name')+1]
        assert '--kubeconfig' in create
    """)
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_files_do_not_depend_on_maintainer_paths():
    public = DOCUMENTS + list((ROOT / "scripts").glob("*.py")) + list((ROOT / "scripts").glob("*.sh"))
    for path in public:
        text = path.read_text()
        assert "/Users/" not in text, path
        assert ".kube/talos" not in text, path
    assert not (ROOT / "package.json").exists()
    assert not (ROOT / "metadata.json").exists()
