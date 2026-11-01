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
        # HTTP readiness can precede Socket.IO itself, as well as setup registration.
        # Probe a read-only RPC until handler registration is complete.
        deadline = time.monotonic() + 60
        while True:
            try:
                if api is None:
                    api = UptimeKumaApi(url, timeout=2)
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


@pytest.mark.integration
def test_real_status_page_preservation_and_lifecycle(live_kuma, body):
    from kuma_operator.config import StatusPageSettings
    from kuma_operator.status_page import read, sync

    if str(live_kuma._parsed_version()) not in ("2.5.8", "2.5.9"):
        pytest.skip("Status page writes are deliberately limited to tested Kuma 2.5.8 and 2.5.9")
    api = live_kuma
    slug = "integration-services"
    parent = child = manual = None
    try:
        parent = api.add_monitor(type="group", name="Parent")["monitorID"]
        child = api.add_monitor(type="group", name="Child", parent=parent)["monitorID"]
        manual = api.add_monitor(type="http", name="Manual", url="https://example.com")["monitorID"]
        api.add_status_page(slug, "Integration services")
        config = api._call("getStatusPage", slug)["config"]
        config.update(autoRefreshInterval=300, customCSS="body { color: inherit; }", footerText="Keep me")
        api._call(
            "saveStatusPage",
            (
                slug,
                config,
                config["icon"],
                [
                    dict(name="Parent", monitorList=[]),
                    dict(name="Parent / Child", monitorList=[]),
                    dict(
                        name="Manual",
                        monitorList=[dict(id=manual, sendUrl=True, url="https://example.com/manual")],
                    ),
                ],
            ),
        )
        settings = StatusPageSettings(slug, config["id"], {})
        baseline_config, groups = read(api, settings)
        settings = StatusPageSettings(slug, config["id"], {parent: groups[0]["id"], child: groups[1]["id"]})
        body["metadata"]["annotations"].update(
            {"uptime-kuma.io/status-page": "true", "uptime-kuma.io/group": str(parent)}
        )
        cfg = parse(body)
        reconcile(api, body, cfg, status_page=settings)
        monitor = next(m for m in api.get_monitors() if m["name"] == cfg["name"])
        assert sync(api, settings, monitor, api.get_monitors(), True) == "unchanged"
        cfg.update(group=str(child), name="renamed-public-service")
        reconcile(api, body, cfg, status_page=settings)
        observed_config, observed = read(api, settings)
        assert observed_config == baseline_config
        assert not observed[0]["monitorList"]
        assert observed[1]["monitorList"][0]["id"] == monitor["id"]
        assert observed[1]["monitorList"][0]["sendUrl"] == 0
        assert "url" not in observed[1]["monitorList"][0]
        assert observed[2] == groups[2]
        api.edit_monitor(parent, name="Renamed parent")
        reconcile(api, body, cfg, status_page=settings)
        assert read(api, settings)[1][1]["name"] == "Renamed parent / Child"
        api.pause_monitor(monitor["id"])
        reconcile(api, body, cfg, status_page=settings)
        assert not read(api, settings)[1][1]["monitorList"]
        api.resume_monitor(monitor["id"])
        reconcile(api, body, cfg, status_page=settings)
        cfg["publish"] = False
        reconcile(api, body, cfg, status_page=settings)
        assert not read(api, settings)[1][1]["monitorList"]
        cfg["publish"] = True
        reconcile(api, body, cfg, status_page=settings)
        reconcile(api, body, None, status_page=settings)
        assert not read(api, settings)[1][1]["monitorList"]
        assert read(api, settings)[0] == baseline_config
    finally:
        # Only this fixture's page and monitors; never accepts a production URL.
        if any(p["slug"] == slug for p in api.get_status_pages()):
            api.delete_status_page(slug)
        reconcile(api, body, None)
        for id_ in (manual, child, parent):
            if id_ is not None:
                api.delete_monitor(id_)


def test_fixture_retries_socket_startup_and_cleans_up(monkeypatch):
    from types import SimpleNamespace
    import sys

    module = sys.modules[__name__]
    monkeypatch.setenv("RUN_KUMA_INTEGRATION", "1")
    commands = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **_: commands.append(args))
    monkeypatch.setattr(module.requests, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    attempts = []
    api = SimpleNamespace(
        need_setup=lambda: True,
        setup=lambda *_: None,
        login=lambda *_: None,
        disconnect=lambda: attempts.append("disconnect"),
    )

    def connect(*_args, **_kwargs):
        attempts.append("connect")
        if attempts.count("connect") == 1:
            raise UptimeKumaException("Socket.IO returns 404 during startup")
        return api

    monkeypatch.setattr(module, "UptimeKumaApi", connect)
    fixture = live_kuma.__wrapped__()
    assert next(fixture) is api
    assert attempts == ["connect", "connect"]
    fixture.close()
    assert attempts[-1] == "disconnect"
    assert commands[0][:2] == ["docker", "run"]
    assert commands[-1][:4] == ["docker", "rm", "-f", "-v"]
