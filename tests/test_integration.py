"""Opt-in, disposable Docker Kuma instances. Never accepts an external URL."""

import os
import secrets
import socket
import subprocess
import time
import copy
import pytest
import requests
from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.exceptions import UptimeKumaException
from kuma_operator.config import parse
from kuma_operator.reconciler import reconcile


@pytest.fixture(scope="module")
def live_kuma():
    if os.getenv("RUN_KUMA_INTEGRATION") != "1":
        pytest.skip("set RUN_KUMA_INTEGRATION=1 for disposable Docker Kuma")
    image = os.getenv("KUMA_TEST_IMAGE", "louislam/uptime-kuma:2.0.2")
    name = "kumaops-test-" + secrets.token_hex(5)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            "UPTIME_KUMA_DB_TYPE=sqlite",
            "-p",
            f"127.0.0.1:{port}:3001",
            image,
        ],
        check=True,
        capture_output=True,
    )
    api = None
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 180
        while True:
            try:
                if requests.get(url, timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("Disposable Kuma did not start")
            time.sleep(1)
        api = UptimeKumaApi(url, timeout=2)
        # The Socket.IO connection can complete before Kuma registers setup.
        # Probe a read-only RPC until handler registration is complete.
        deadline = time.monotonic() + 60
        while True:
            try:
                assert api.need_setup(), "Disposable Kuma must be fresh"
                break
            except UptimeKumaException:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        api.timeout = 10
        password = secrets.token_urlsafe(24)
        api.setup("test-user", password)
        api.login("test-user", password)
        yield api
    finally:
        if api:
            api.disconnect()
        subprocess.run(["docker", "rm", "-f", "-v", name], check=True, capture_output=True)


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["http", "tcp", "ping", "dns"])
def test_real_kuma_lifecycle(live_kuma, body, kind):
    body = copy.deepcopy(body)
    body["metadata"]["name"] = "app-" + kind
    body["metadata"]["uid"] = "uid-" + kind
    body["metadata"]["annotations"].update(
        {"uptime-kuma.io/type": kind, "uptime-kuma.io/hostname": "localhost"}
    )
    cfg = parse(body)
    assert reconcile(live_kuma, body, cfg) == "created"
    assert reconcile(live_kuma, body, cfg) == "unchanged"
    cfg["name"] += "-renamed"
    assert reconcile(live_kuma, body, cfg) == "updated"
    assert reconcile(live_kuma, body, cfg) == "unchanged"
    assert reconcile(live_kuma, body, None, deleting=True) == "deleted"
    assert reconcile(live_kuma, body, None, deleting=True) == "absent"


@pytest.mark.integration
def test_real_group_notifications_and_missing_reference(live_kuma, body):
    from uptime_kuma_api import MonitorType

    group_id = live_kuma.add_monitor(type=MonitorType.GROUP, name="integration-parent")["monitorID"]
    notification_id = live_kuma.add_notification(
        name="integration-webhook",
        type="webhook",
        isDefault=False,
        applyExisting=False,
        webhookURL="http://127.0.0.1:9",
        webhookContentType="json",
    )["id"]
    cfg = parse(body)
    cfg.update(group=str(group_id), notifications=["integration-webhook"])
    try:
        assert reconcile(live_kuma, body, cfg) == "created"
        monitor = next(m for m in live_kuma.get_monitors() if m["name"] == cfg["name"])
        assert monitor["parent"] == group_id
        assert monitor["notificationIDList"] == [notification_id]
        cfg["notifications"] = ["missing-provider"]
        with pytest.raises(LookupError):
            reconcile(live_kuma, body, cfg)
        assert live_kuma.get_monitor(monitor["id"])["notificationIDList"] == [notification_id]
        cfg.update(group="", notifications=[])
        assert reconcile(live_kuma, body, cfg) == "updated"
        assert live_kuma.get_monitor(monitor["id"])["parent"] is None
        assert live_kuma.get_monitor(monitor["id"])["notificationIDList"] == []
    finally:
        reconcile(live_kuma, body, None, deleting=True)
        live_kuma.delete_monitor(group_id)
        live_kuma.delete_notification(notification_id)


@pytest.mark.integration
def test_real_type_change_preserves_monitor_id(live_kuma, body):
    cfg = parse(body)
    assert reconcile(live_kuma, body, cfg) == "created"
    monitor = next(m for m in live_kuma.get_monitors() if m["name"] == cfg["name"])
    try:
        cfg = {k: v for k, v in cfg.items() if k not in ("url", "accepted_statuscodes")}
        cfg.update(type="port", hostname="localhost", port=80)
        assert reconcile(live_kuma, body, cfg) == "updated"
        observed = live_kuma.get_monitor(monitor["id"])
        assert observed["type"] == "port"
        assert observed["name"] == cfg["name"]
    finally:
        reconcile(live_kuma, body, None, deleting=True)
