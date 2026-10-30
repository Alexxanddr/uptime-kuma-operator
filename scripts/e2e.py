"""Run operator/Kuma E2E in a disposable namespace on an explicitly selected cluster.

Select a kubeconfig through --kubeconfig or KUBECONFIG. Requires --run.
The source deployment avoids pushing an unreviewed image to a public registry.
"""

import argparse
import json
import sys
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import requests
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.exceptions import UptimeKumaException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kuma_operator.client import KumaManager  # noqa: E402
from kuma_operator.config import Settings  # noqa: E402

PREFIX = "kumaops-e2e.test"


class ForwardedKuma(UptimeKumaApi):
    """Avoid WebSocket upgrade across kubectl multiplexed port forwarding."""

    def connect(self):
        self.sio.connect(
            f"{self.url}/socket.io/", transports=["polling"], wait_timeout=self.timeout, headers=self.headers
        )


class ManagedKuma:
    def __init__(self, url, password):
        self.manager = KumaManager(Settings(url, "test-user", password, 10), factory=ForwardedKuma)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            with self.manager.session() as api:
                return getattr(api, name)(*args, **kwargs)

        return call

    def disconnect(self):
        self.manager.close()


def wait_for(check, description, seconds=240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(2)
    raise TimeoutError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--kubeconfig",
        default=os.getenv("KUBECONFIG"),
        help="Explicit kubeconfig for the authorized test cluster",
    )
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; creates and deletes an isolated test namespace")
    if not args.kubeconfig:
        parser.error("Set KUBECONFIG or pass --kubeconfig; no cluster is selected implicitly")
    kubeconfig = str(Path(args.kubeconfig).expanduser().resolve())
    os.environ["KUBECONFIG"] = kubeconfig
    config.load_kube_config(config_file=kubeconfig)
    core, apps, rbac = client.CoreV1Api(), client.AppsV1Api(), client.RbacAuthorizationV1Api()
    token = secrets.token_hex(5)
    ns = "kumaops-e2e-" + token
    password = secrets.token_urlsafe(24)
    core.create_namespace({"metadata": {"name": ns, "labels": {"kumaops-test": token}}})
    forward = None
    kuma = None
    try:
        core.create_namespaced_service_account(ns, {"metadata": {"name": "operator"}})
        rbac.create_namespaced_role(
            ns,
            {
                "metadata": {"name": "operator"},
                "rules": [
                    {
                        "apiGroups": ["apps"],
                        "resources": ["deployments", "statefulsets", "daemonsets"],
                        "verbs": ["get", "list", "watch", "patch"],
                    },
                    {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "patch"]},
                ],
            },
        )
        rbac.create_namespaced_role_binding(
            ns,
            {
                "metadata": {"name": "operator"},
                "subjects": [{"kind": "ServiceAccount", "name": "operator", "namespace": ns}],
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "operator"},
            },
        )
        sources = {p.name: p.read_text() for p in (ROOT / "kuma_operator").glob("*.py")}
        sources["entrypoint.py"] = (ROOT / "operator.py").read_text()
        sources["requirements.txt"] = (ROOT / "requirements.txt").read_text()
        core.create_namespaced_config_map(ns, {"metadata": {"name": "source"}, "data": sources})
        core.create_namespaced_secret(
            ns, {"metadata": {"name": "credentials"}, "stringData": {"password": password}}
        )
        kuma_name = ns + "-kuma"
        operator_name = ns + "-operator"

        def deployment(name, container, **extra):
            return {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": name},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": name}},
                    "template": {
                        "metadata": {"labels": {"app": name}},
                        "spec": {"containers": [container], **extra},
                    },
                },
            }

        apps.create_namespaced_deployment(
            ns,
            deployment(
                kuma_name,
                {
                    "name": "kuma",
                    "image": "louislam/uptime-kuma:2.0.2",
                    "env": [{"name": "UPTIME_KUMA_DB_TYPE", "value": "sqlite"}],
                    "ports": [{"containerPort": 3001}],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "128Mi"},
                        "limits": {"cpu": "1", "memory": "512Mi"},
                    },
                    "volumeMounts": [{"name": "data", "mountPath": "/app/data"}],
                    "readinessProbe": {"httpGet": {"path": "/", "port": 3001}, "periodSeconds": 3},
                },
                volumes=[{"name": "data", "emptyDir": {}}],
                automountServiceAccountToken=False,
            ),
        )
        core.create_namespaced_service(
            ns,
            {
                "metadata": {"name": kuma_name},
                "spec": {"selector": {"app": kuma_name}, "ports": [{"port": 3001, "targetPort": 3001}]},
            },
        )
        wait_for(
            lambda: apps.read_namespaced_deployment(kuma_name, ns).status.ready_replicas, "Kuma readiness"
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        forward = subprocess.Popen(
            ["kubectl", "port-forward", "-n", ns, "service/" + kuma_name, f"{port}:3001"],
            env={**os.environ, "KUBECONFIG": kubeconfig},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"

        def reachable():
            try:
                return requests.get(url, timeout=2).status_code == 200
            except requests.RequestException:
                return False

        wait_for(reachable, "Kuma port-forward", 30)
        kuma = ForwardedKuma(url, timeout=2)

        def setup_ready():
            try:
                return kuma.need_setup()
            except UptimeKumaException:
                return False

        wait_for(setup_ready, "Kuma setup RPC readiness", 60)
        kuma.timeout = 30
        kuma.setup("test-user", password)
        kuma.login("test-user", password)
        kuma.disconnect()
        kuma = ManagedKuma(url, password)
        mounts = [
            {"name": "source", "mountPath": "/app/operator.py", "subPath": "entrypoint.py"},
            {"name": "source", "mountPath": "/app/requirements.txt", "subPath": "requirements.txt"},
        ]
        mounts += [
            {"name": "source", "mountPath": "/app/kuma_operator/" + name, "subPath": name}
            for name in sources
            if name.endswith(".py") and name != "entrypoint.py"
        ]
        env = [
            {"name": k, "value": v}
            for k, v in {
                "KUMA_URL": f"http://{kuma_name}:3001",
                "KUMA_USER": "test-user",
                "KUMA_ANNOTATION_PREFIX": PREFIX,
                "KUMA_RESYNC_INTERVAL": "5",
                "PYTHONPATH": "/tmp/deps:/app",
                "HOME": "/tmp",
                "USER": "kumaoperator",
                "PYTHONDONTWRITEBYTECODE": "1",
            }.items()
        ]
        env.append(
            {"name": "KUMA_PASS", "valueFrom": {"secretKeyRef": {"name": "credentials", "key": "password"}}}
        )
        container = {
            "name": "operator",
            "image": "python:3.11-slim",
            "workingDir": "/app",
            "command": [
                "sh",
                "-c",
                f"pip install --quiet --no-cache-dir --target /tmp/deps -r /app/requirements.txt && exec python -m kopf run --namespace={ns} --standalone --liveness=http://0.0.0.0:8080/healthz /app/operator.py",
            ],
            "env": env,
            "volumeMounts": mounts + [{"name": "tmp", "mountPath": "/tmp"}],
            "resources": {
                "requests": {"cpu": "50m", "memory": "128Mi"},
                "limits": {"cpu": "1", "memory": "512Mi"},
            },
            "securityContext": {
                "runAsUser": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
                "runAsNonRoot": True,
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            },
            "readinessProbe": {"httpGet": {"path": "/healthz", "port": 8080}, "periodSeconds": 3},
        }
        apps.create_namespaced_deployment(
            ns,
            deployment(
                operator_name,
                container,
                serviceAccountName="operator",
                volumes=[
                    {"name": "source", "configMap": {"name": "source"}},
                    {"name": "tmp", "emptyDir": {}},
                ],
            ),
        )
        wait_for(
            lambda: apps.read_namespaced_deployment(operator_name, ns).status.ready_replicas,
            "Operator readiness",
        )

        def monitors():
            return [m for m in kuma.get_monitors() if m.get("description", "").startswith("k8s:v2:")]

        for kind, plural in [
            ("Deployment", "deployment"),
            ("StatefulSet", "stateful_set"),
            ("DaemonSet", "daemon_set"),
        ]:
            name = ns + "-" + kind.lower()
            labels = {"app": name}
            annotations = {
                PREFIX + "/enabled": "true",
                PREFIX + "/url": "https://example.com",
                PREFIX + "/name": name,
            }
            spec = {
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {"containers": [{"name": "pause", "image": "registry.k8s.io/pause:3.10"}]},
                },
            }
            if kind == "DaemonSet":
                spec["template"]["spec"]["nodeSelector"] = {"kumaops-e2e-no-node": token}
            else:
                spec["replicas"] = 0
            if kind == "StatefulSet":
                spec["serviceName"] = name
            resource = {
                "apiVersion": "apps/v1",
                "kind": kind,
                "metadata": {"name": name, "annotations": annotations},
                "spec": spec,
            }
            getattr(apps, "create_namespaced_" + plural)(ns, resource)
            wait_for(lambda: any(m["name"] == name for m in monitors()), "Monitor creation " + kind)
            current = next(m for m in monitors() if m["name"] == name)
            id_ = current["id"]
            # Invalid desired state must preserve the monitor.
            getattr(apps, "patch_namespaced_" + plural)(
                name, ns, {"metadata": {"annotations": {PREFIX + "/interval": "invalid"}}}
            )
            wait_for(
                lambda: getattr(apps, "read_namespaced_" + plural)(name, ns).metadata.annotations.get(
                    PREFIX + "/error"
                ),
                "Validation status",
            )
            assert kuma.get_monitor(id_)["name"] == name
            renamed = name + "-renamed"
            getattr(apps, "patch_namespaced_" + plural)(
                name,
                ns,
                {"metadata": {"annotations": {PREFIX + "/interval": "60", PREFIX + "/name": renamed}}},
            )
            wait_for(lambda: kuma.get_monitor(id_)["name"] == renamed, "Rename recovery")
            kuma.edit_monitor(id_, interval=90)
            wait_for(lambda: kuma.get_monitor(id_)["interval"] == 60, "External drift recovery")
            # Both disabling and resource deletion are exercised.
            if kind == "StatefulSet":
                getattr(apps, "patch_namespaced_" + plural)(
                    name, ns, {"metadata": {"annotations": {PREFIX + "/enabled": "false"}}}
                )
                wait_for(lambda: all(m["id"] != id_ for m in monitors()), "Disable cleanup")
                getattr(apps, "patch_namespaced_" + plural)(
                    name, ns, {"metadata": {"annotations": {PREFIX + "/enabled": "true"}}}
                )
                wait_for(lambda: any(m["name"] == renamed for m in monitors()), "Re-enable")
                id_ = next(m["id"] for m in monitors() if m["name"] == renamed)
            getattr(apps, "delete_namespaced_" + plural)(name, ns)
            wait_for(lambda: all(m["id"] != id_ for m in monitors()), "Finalizer cleanup")

            def resource_gone():
                try:
                    getattr(apps, "read_namespaced_" + plural)(name, ns)
                    return False
                except ApiException as exc:
                    if exc.status == 404:
                        return True
                    raise

            wait_for(resource_gone, "Kubernetes finalizer release")
            print(kind + ": create, invalid config, recovery, rename, drift, delete PASS", flush=True)
        assert not monitors()
    except Exception:
        pods = core.list_namespaced_pod(ns).items
        for pod in pods:
            if pod.metadata.name.startswith(ns + "-"):
                try:
                    print(core.read_namespaced_pod_log(pod.metadata.name, ns, tail_lines=60))
                except ApiException:
                    pass
        raise
    finally:
        if kuma:
            try:
                kuma.disconnect()
            except Exception:
                print("Test client disconnect failed; continuing resource cleanup", flush=True)
        if forward:
            forward.terminate()
            try:
                forward.wait(timeout=10)
            except subprocess.TimeoutExpired:
                forward.kill()
                forward.wait(timeout=10)
        # Never delete a namespace not carrying this run's unique ownership label.
        if core.read_namespace(ns).metadata.labels.get("kumaops-test") == token:
            # Only our disposable fixtures: production finalizers are untouched.
            for plural in ("deployment", "stateful_set", "daemon_set"):
                for resource in getattr(apps, "list_namespaced_" + plural)(ns).items:
                    own = {f"{PREFIX}/KopfFinalizerMarker", f"kopf.{PREFIX}/KopfFinalizerMarker"}
                    finalizers = resource.metadata.finalizers or []
                    if any(f in own for f in finalizers):
                        operations = [
                            {"op": "test", "path": "/metadata/uid", "value": resource.metadata.uid},
                            {
                                "op": "replace",
                                "path": "/metadata/finalizers",
                                "value": [f for f in finalizers if f not in own],
                            },
                        ]
                        subprocess.run(
                            [
                                "kubectl",
                                "patch",
                                plural.replace("_", ""),
                                resource.metadata.name,
                                "--namespace",
                                ns,
                                "--type=json",
                                "-p",
                                json.dumps(operations),
                            ],
                            env={**os.environ, "KUBECONFIG": kubeconfig},
                            check=True,
                            capture_output=True,
                        )
            core.delete_namespace(ns)

            def namespace_gone():
                try:
                    core.read_namespace(ns)
                    return False
                except ApiException as exc:
                    if exc.status == 404:
                        return True
                    raise

            wait_for(namespace_gone, "Namespace cleanup", 120)
            print("Cleanup verified for " + ns, flush=True)
        else:
            raise RuntimeError("Namespace ownership changed; automatic cleanup refused")
    print("E2E PASS namespace=" + ns, flush=True)


if __name__ == "__main__":
    main()
