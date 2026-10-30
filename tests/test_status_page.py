import copy
import pytest
from kuma_operator.config import InvalidConfig, StatusPageSettings, parse
from kuma_operator.reconciler import identity, reconcile
from kuma_operator import status_page as page


@pytest.fixture
def setup(api, body, monkeypatch):
    api.monitors = [
        dict(id=100, type="group", name="Parent", parent=None),
        dict(id=101, type="group", name="Child", parent=100),
    ]
    api.config = dict(
        id=7,
        slug="services",
        icon="/icon.svg",
        autoRefreshInterval=300,
        title="Keep",
        customCSS="body{}",
        analyticsType=None,
    )
    api.groups = [
        dict(id=8, name="Parent", monitorList=[]),
        dict(id=9, name="Parent / Child", monitorList=[]),
        dict(id=10, name="Manual", monitorList=[dict(id=999, sendUrl=True, url="https://example.com")]),
    ]
    api.page_writes = []

    def read(*_):
        return copy.deepcopy((api.config, api.groups))

    def call(event, data):
        assert event == "saveStatusPage"
        _, config, _, groups = data
        api.config, api.groups = copy.deepcopy((config, groups))
        api.page_writes.append(copy.deepcopy(groups))

    api._call = call
    monkeypatch.setattr(page, "read", read)
    body["metadata"]["annotations"].update(
        {"uptime-kuma.io/status-page": "true", "uptime-kuma.io/group": "100"}
    )
    return api, body, StatusPageSettings("services", 7, {100: 8, 101: 9})


def test_lifecycle_order_manual_configuration_and_hierarchy(setup):
    api, body, settings = setup
    manual = copy.deepcopy(api.groups[-1])
    config = copy.deepcopy(api.config)
    cfg = parse(body)
    assert reconcile(api, body, cfg, status_page=settings) == "created"
    monitor_id = next(m["id"] for m in api.monitors if m.get("description") == identity(body))
    assert api.groups[0]["monitorList"] == [dict(id=monitor_id, sendUrl=False)]
    assert reconcile(api, body, cfg, status_page=settings) == "unchanged"
    assert len(api.page_writes) == 1
    cfg.update(group="101", name="renamed")
    reconcile(api, body, cfg, status_page=settings)
    assert api.groups[0]["monitorList"] == []
    assert api.groups[1]["monitorList"] == [dict(id=monitor_id, sendUrl=False)]
    api.monitors[0]["name"] = "Renamed parent"
    reconcile(api, body, cfg, status_page=settings)
    assert api.groups[1]["name"] == "Renamed parent / Child"
    api.get_monitor(monitor_id)
    next(m for m in api.monitors if m["id"] == monitor_id)["active"] = False
    reconcile(api, body, cfg, status_page=settings)
    assert api.groups[1]["monitorList"] == []
    next(m for m in api.monitors if m["id"] == monitor_id)["active"] = True
    reconcile(api, body, cfg, status_page=settings)
    cfg["publish"] = False
    reconcile(api, body, cfg, status_page=settings)
    assert api.groups[1]["monitorList"] == []
    cfg["publish"] = True
    reconcile(api, body, cfg, status_page=settings)
    reconcile(api, body, None, status_page=settings)
    assert api.groups[1]["monitorList"] == []
    assert api.groups[-1] == manual
    assert api.config == config


def test_existing_manual_order_is_preserved(setup):
    api, body, settings = setup
    cfg = parse(body)
    reconcile(api, body, cfg, status_page=settings)
    other = copy.deepcopy(body)
    other["metadata"].update(name="other", uid="other")
    reconcile(api, other, parse(other), status_page=settings)
    api.groups[0]["monitorList"].reverse()
    before = copy.deepcopy(api.groups)
    reconcile(api, body, cfg, status_page=settings)
    assert api.groups == before


@pytest.mark.parametrize("problem", ["hidden-url", "manual-link", "missing-target", "cycle"])
def test_conflicts_fail_closed(setup, problem):
    api, body, settings = setup
    cfg = parse(body)
    reconcile(api, body, cfg, status_page=settings)
    if problem == "hidden-url":
        api.groups[-1]["monitorList"][0] = dict(id=999, sendUrl=False)
    elif problem == "manual-link":
        api.groups[0]["monitorList"][0].update(sendUrl=True, url="https://example.com")
    elif problem == "missing-target":
        api.groups.pop(1)
    else:
        api.monitors[0]["parent"] = 101
    before = copy.deepcopy(api.groups)
    with pytest.raises(InvalidConfig):
        reconcile(api, body, cfg, status_page=settings)
    assert api.groups == before
    assert len(api.page_writes) == 1


def test_lost_ack_does_not_duplicate_and_failure_blocks_delete(setup):
    api, body, settings = setup
    original = api._call

    def commit_then_timeout(*args):
        original(*args)
        raise TimeoutError

    api._call = commit_then_timeout
    with pytest.raises(TimeoutError):
        reconcile(api, body, parse(body), status_page=settings)
    api._call = original
    reconcile(api, body, parse(body), status_page=settings)
    assert len(api.groups[0]["monitorList"]) == 1
    api._call = lambda *_: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TimeoutError):
        reconcile(api, body, None, status_page=settings)
    assert any(m.get("description") == identity(body) for m in api.monitors)
    api._call = original
    reconcile(api, body, None, status_page=settings)
    assert not api.groups[0]["monitorList"]


def test_concurrent_change_is_not_overwritten(setup, monkeypatch):
    api, body, settings = setup
    original = page.read
    reads = 0

    def changing(*args):
        nonlocal reads
        reads += 1
        if reads == 2:
            api.config["title"] = "Changed by administrator"
        return original(*args)

    monkeypatch.setattr(page, "read", changing)
    with pytest.raises(InvalidConfig):
        reconcile(api, body, parse(body), status_page=settings)
    assert not api.page_writes


def test_partial_save_is_repaired(setup):
    api, body, settings = setup
    original = api._call

    def partial(event, data):
        original(event, data)
        api.groups[0]["monitorList"] = []

    api._call = partial
    with pytest.raises(InvalidConfig):
        reconcile(api, body, parse(body), status_page=settings)
    api._call = original
    reconcile(api, body, parse(body), status_page=settings)
    assert len(api.groups[0]["monitorList"]) == 1


def test_unmapped_parent_rejected_before_monitor_creation(setup):
    api, body, settings = setup
    cfg = parse(body)
    cfg["group"] = ""
    with pytest.raises(InvalidConfig):
        reconcile(api, body, cfg, status_page=settings)
    assert not api.writes


@pytest.mark.parametrize("mapping", ["{}", '{"x":8}', '{"1":true}', '{"1":8,"2":8}', "[]", "bad"])
def test_invalid_settings(monkeypatch, mapping):
    monkeypatch.setenv("KUMA_STATUS_PAGE_SLUG", "services")
    monkeypatch.setenv("KUMA_STATUS_PAGE_ID", "7")
    monkeypatch.setenv("KUMA_STATUS_PAGE_GROUPS", mapping)
    with pytest.raises(InvalidConfig):
        StatusPageSettings.from_env()


def test_optin_and_disabled_default(body, monkeypatch):
    assert not parse(body)["publish"]
    assert StatusPageSettings.from_env() is None
    body["metadata"]["annotations"]["uptime-kuma.io/status-page"] = "yes"
    with pytest.raises(InvalidConfig):
        parse(body)
    monkeypatch.setenv("KUMA_STATUS_PAGE_SLUG", "services")
    with pytest.raises(InvalidConfig):
        StatusPageSettings.from_env()


def test_read_preserves_admin_fields_and_bypasses_cache(monkeypatch):
    from types import SimpleNamespace

    config = dict(id=7, slug="services", icon="/icon.svg", autoRefreshInterval=300, domainNameList=[])
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return dict(config={k: v for k, v in config.items() if k != "domainNameList"}, publicGroupList=[])

    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(page.requests, "get", get)
    api = SimpleNamespace(
        _parsed_version=lambda: "2.5.8",
        _call=lambda *_: dict(config=config),
        url="http://kuma.test",
        timeout=3,
        ssl_verify=True,
        headers={},
    )
    settings = StatusPageSettings("services", 7, {0: 8})
    assert page.read(api, settings) == (config, [])
    page.read(api, settings)
    assert calls[0]["params"] != calls[1]["params"]
    api._parsed_version = lambda: "2.0.2"
    with pytest.raises(InvalidConfig):
        page.read(api, settings)
    api._parsed_version = lambda: "2.5.8"
    with pytest.raises(InvalidConfig):
        page.read(api, StatusPageSettings("services", 999, {0: 8}))


def test_explicit_existing_id_allowlist_and_annotation_override(setup):
    api, body, settings = setup
    body["metadata"]["annotations"].pop("uptime-kuma.io/status-page")
    reconcile(api, body, parse(body))
    id_ = next(m["id"] for m in api.monitors if m.get("description") == identity(body))
    settings = StatusPageSettings(settings.slug, settings.id, settings.groups, frozenset([id_]))
    reconcile(api, body, parse(body), status_page=settings)
    assert api.groups[0]["monitorList"] == [dict(id=id_, sendUrl=False)]
    body["metadata"]["annotations"]["uptime-kuma.io/status-page"] = "false"
    reconcile(api, body, parse(body), status_page=settings)
    assert not api.groups[0]["monitorList"]
    body["metadata"]["annotations"].pop("uptime-kuma.io/status-page")
    reconcile(api, body, parse(body), status_page=settings)
    reconcile(
        api, body, parse(body), status_page=StatusPageSettings(settings.slug, settings.id, settings.groups)
    )
    assert not api.groups[0]["monitorList"]


@pytest.mark.parametrize(
    "marker", [None, "manual", 'k8s:v2:{"a":1,"b":2,"c":3,"d":4}', 'k8s:v2:["Deployment","ns","name",""]']
)
def test_invalid_association_ownership(marker):
    assert not page.owned_marker(marker)


def test_status_allowlist_validation_and_roundtrip(monkeypatch):
    monkeypatch.setenv("KUMA_STATUS_PAGE_SLUG", "services")
    monkeypatch.setenv("KUMA_STATUS_PAGE_ID", "7")
    monkeypatch.setenv("KUMA_STATUS_PAGE_GROUPS", '{"100":8}')
    monkeypatch.setenv("KUMA_STATUS_PAGE_MONITORS", "[1,2]")
    assert StatusPageSettings.from_env().monitors == frozenset([1, 2])
    monkeypatch.setenv("KUMA_STATUS_PAGE_MONITORS", "[true]")
    with pytest.raises(InvalidConfig):
        StatusPageSettings.from_env()
