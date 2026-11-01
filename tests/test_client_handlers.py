import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import kopf
import pytest
from kuma_operator.client import KumaManager
from kuma_operator.config import Settings
from kuma_operator import handlers
from kuma_operator.reconciler import MANAGED


class Connection:
    def __init__(self, *args, **kwargs):
        self.closed = False

    def login(self, *args):
        pass

    def disconnect(self):
        self.closed = True


def test_client_serializes_and_recovers():
    manager = KumaManager(Settings("http://test", "u", "p"), factory=Connection)
    entered = threading.Event()
    release = threading.Event()
    second = threading.Event()

    def first():
        with manager.session():
            entered.set()
            assert release.wait(3)

    def next_():
        assert entered.wait(3)
        with manager.session():
            second.set()

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(first)
        b = pool.submit(next_)
        assert entered.wait(3)
        assert not second.is_set()
        release.set()
        a.result()
        b.result()
    original = manager.api
    with pytest.raises(TimeoutError):
        with manager.session():
            raise TimeoutError
    assert original.closed
    assert manager.api is None
    with manager.session() as api:
        assert api is not original
    manager.close()
    assert api.closed


@pytest.fixture
def installed(monkeypatch, api):
    class Manager:
        @contextmanager
        def session(self):
            yield api

    monkeypatch.setattr(handlers, "manager", Manager())
    return api


def run(body, **kwargs):
    patch = kopf.Patch()
    handlers.handle(body, patch, logging.getLogger("test"), **kwargs)
    return patch


def test_handler_durable_marker_then_create(body, installed):
    patch = run(body)
    assert patch["metadata"]["annotations"][MANAGED] == "true"
    assert not installed.monitors
    body["metadata"]["annotations"][MANAGED] = "true"
    patch = run(body)
    assert len(installed.monitors) == 1
    assert patch["metadata"]["annotations"]["uptime-kuma.io/state"] == "created"
    body["metadata"]["annotations"].pop("uptime-kuma.io/enabled")
    assert handlers.selected(body)
    patch = run(body)
    assert not installed.monitors
    assert patch["metadata"]["annotations"][MANAGED] is None


def test_invalid_does_not_delete(body, installed):
    body["metadata"]["annotations"][MANAGED] = "true"
    run(body)
    body["metadata"]["annotations"]["uptime-kuma.io/interval"] = "bad"
    with pytest.raises(kopf.TemporaryError):
        run(body)
    assert len(installed.monitors) == 1
    assert len(installed.writes) == 1


def test_failure_backoff_and_recovery(body, installed):
    body["metadata"]["annotations"][MANAGED] = "true"
    installed.fail = True
    with pytest.raises(kopf.TemporaryError) as exc:
        run(body, retry=30)
    assert exc.value.delay == 300
    installed.fail = False
    run(body)
    assert len(installed.monitors) == 1


def test_delete_finalizer_cleanup(body, installed):
    body["metadata"]["annotations"][MANAGED] = "true"
    run(body)
    handlers.delete(body, kopf.Patch(), logging.getLogger("test"))
    assert not installed.monitors


def test_unmanaged_not_selected(body):
    body["metadata"]["annotations"] = {}
    assert not handlers.selected(body)


def test_startup_shutdown(monkeypatch):
    for key, value in [("KUMA_URL", "http://test"), ("KUMA_USER", "u"), ("KUMA_PASS", "p")]:
        monkeypatch.setenv(key, value)
    settings = kopf.OperatorSettings()
    handlers.startup(settings)
    assert settings.execution.max_workers == 1
    assert isinstance(settings.persistence.progress_storage, kopf.AnnotationsProgressStorage)
    import asyncio

    assert asyncio.run(handlers.probe())["configured"]
    handlers.shutdown()


def test_disconnected_socket_reconnects():
    from types import SimpleNamespace

    manager = KumaManager(Settings("http://test", "u", "secret"), factory=Connection)
    with manager.session() as first:
        first.sio = SimpleNamespace(connected=False)
    with manager.session() as second:
        assert second is not first
    assert first.closed
    manager.close()


def test_login_failure_disconnects():
    created = []

    class FailingLogin(Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def login(self, *args):
            raise PermissionError

    manager = KumaManager(Settings("http://test", "u", "secret"), factory=FailingLogin)
    with pytest.raises(PermissionError):
        with manager.session():
            pytest.fail("Must not enter an unauthenticated session")
    assert manager.api is None
    assert created[0].closed
    assert "secret" not in repr(manager.settings)


def test_socket_reconnect_requires_new_authenticated_session():
    from types import SimpleNamespace

    authenticated = []

    class SocketConnection(Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sio = SimpleNamespace(connected=True, reconnection=True)

        def login(self, *args):
            authenticated.append(self)

    manager = KumaManager(Settings("http://test", "u", "secret"), factory=SocketConnection)
    with manager.session() as first:
        assert first.sio.reconnection is False
    first.sio.connected = False
    with manager.session() as second:
        assert second is not first
        assert second.sio.reconnection is False
    assert authenticated == [first, second]
    assert first.closed
    manager.close()


def test_health_probe_responds_when_handler_workers_are_saturated():
    import asyncio
    from kopf._core.actions import invocation

    entered = threading.Event()
    release = threading.Event()

    def block():
        entered.set()
        release.wait(5)

    settings = kopf.OperatorSettings()
    with ThreadPoolExecutor(max_workers=1) as pool:
        settings.execution.executor = pool
        occupied = pool.submit(block)
        assert entered.wait(1)

        async def request():
            return await asyncio.wait_for(invocation.invoke(handlers.probe, settings=settings), 0.5)

        try:
            assert "configured" in asyncio.run(request())
        finally:
            release.set()
            occupied.result()


def test_controller_state_does_not_trigger_desired_state_changes(body, monkeypatch):
    import copy

    for key, value in [("KUMA_URL", "http://test"), ("KUMA_USER", "u"), ("KUMA_PASS", "p")]:
        monkeypatch.setenv(key, value)
    settings = kopf.OperatorSettings()
    handlers.startup(settings)
    storage = settings.persistence.diffbase_storage
    original = storage.build(body=kopf.Body(body))
    observed = copy.deepcopy(body)
    observed["metadata"]["generation"] += 1
    observed["metadata"]["annotations"].update(
        {
            "uptime-kuma.io/managed": "true",
            "uptime-kuma.io/state": "unchanged",
            "uptime-kuma.io/config-hash": "recorded",
            "uptime-kuma.io/observed-generation": "2",
            "uptime-kuma.io/error": "old error",
        }
    )
    assert storage.build(body=kopf.Body(observed)) == original
    assert observed["metadata"]["annotations"]["uptime-kuma.io/managed"] == "true"
    observed["metadata"]["annotations"]["uptime-kuma.io/url"] = "https://changed.example.com"
    assert storage.build(body=kopf.Body(observed)) != original
