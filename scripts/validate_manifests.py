"""Validate manifests using the Kubernetes OpenAPI schemas (no cluster needed)."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# Pinned validator version. stdin contains only public example manifests.
files = sorted((ROOT / "kubernetes").glob("*.yaml"))
result = subprocess.run(
    [
        "docker",
        "run",
        "--rm",
        "-i",
        "ghcr.io/yannh/kubeconform:v0.7.0",
        "-strict",
        "-summary",
        "-kubernetes-version",
        "1.32.0",
    ],
    input="\n---\n".join(p.read_text() for p in files),
    text=True,
)
raise SystemExit(result.returncode)
