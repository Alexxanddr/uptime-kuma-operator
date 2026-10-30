import copy
import pytest
from kuma_operator.config import parse
from kuma_operator.reconciler import OwnershipConflict, identity, reconcile


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_lifecycle(api, body, kind):
    body["kind"] = kind
    cfg = parse(body)
    assert reconcile(api, body, cfg) == "created"
    id_ = api.monitors[0]["id"]
    assert reconcile(api, body, cfg) == "unchanged"
    assert len(api.writes) == 1
    cfg["name"] = "renamed"
    assert reconcile(api, body, cfg) == "updated"
    assert api.monitors[0]["id"] == id_
    api.monitors[0]["interval"] = 999
    assert reconcile(api, body, cfg) == "updated"
    assert api.monitors[0]["interval"] == 60
    assert reconcile(api, body, None) == "deleted"
    assert reconcile(api, body, None, deleting=True) == "absent"


def test_cleanup_does_not_match_prefix_name_or_url(api, body):
    api.monitors = [
        dict(id=1, name="k8s-test-app", description="k8s:test/app-other", url="http://app.test.svc")
    ]
    assert reconcile(api, body, None, deleting=True) == "absent"
    assert len(api.monitors) == 1


def test_foreign_name_not_adopted(api, body):
    api.monitors = [dict(id=1, name="k8s-test-app", description="manual")]
    with pytest.raises(OwnershipConflict):
        reconcile(api, body, parse(body))
    assert not api.writes


def test_legacy_requires_optin_and_preserves_id(api, body):
    api.monitors = [dict(id=22, name="legacy", description="k8s:test/app")]
    with pytest.raises(OwnershipConflict):
        reconcile(api, body, parse(body))
    body["metadata"]["annotations"]["uptime-kuma.io/adopt-legacy"] = "true"
    assert reconcile(api, body, parse(body)) == "updated"
    assert api.monitors[0]["id"] == 22
    assert api.monitors[0]["description"] == identity(body)


def test_ambiguous_owner(api, body):
    api.monitors = [dict(id=i, name=str(i), description=identity(body)) for i in [1, 2]]
    with pytest.raises(OwnershipConflict):
        reconcile(api, body, None, deleting=True)
    assert not api.writes


@pytest.mark.parametrize("field,value", [("kind", "StatefulSet"), ("uid", "uid-2"), ("namespace", "another")])
def test_other_resource_not_deleted(api, body, field, value):
    reconcile(api, body, parse(body))
    other = copy.deepcopy(body)
    if field == "kind":
        other[field] = value
    else:
        other["metadata"][field] = value
    assert reconcile(api, other, None, deleting=True) == "absent"
    assert len(api.monitors) == 1


def test_references_resolved_before_mutation(api, body):
    cfg = parse(body)
    reconcile(api, body, cfg)
    cfg["notifications"] = ["missing"]
    with pytest.raises(LookupError):
        reconcile(api, body, cfg)
    assert len(api.writes) == 1
    api.notifications = [dict(id=7, name="missing")]
    cfg["group"] = "group"
    with pytest.raises(LookupError):
        reconcile(api, body, cfg)
    api.monitors.append(dict(id=100, name="group", type="group"))
    assert reconcile(api, body, cfg) == "updated"
    monitor = api.get_monitor(1)
    assert monitor["notificationIDList"] == [7]
    assert monitor["parent"] == 100
    # Kuma may serialize IDs as keys and parent as a string.
    api.monitors[0]["notificationIDList"] = {"7": True}
    api.monitors[0]["parent"] = "100"
    assert reconcile(api, body, cfg) == "unchanged"


def test_outage_recovery(api, body):
    api.fail = True
    with pytest.raises(TimeoutError):
        reconcile(api, body, parse(body))
    api.fail = False
    assert reconcile(api, body, parse(body)) == "created"


def test_ambiguous_create_response_is_recovered_without_duplicate(api, body):
    original = api.add_monitor

    def commit_then_timeout(**kwargs):
        original(**kwargs)
        raise TimeoutError("ack lost after remote commit")

    api.add_monitor = commit_then_timeout
    with pytest.raises(TimeoutError):
        reconcile(api, body, parse(body))
    api.add_monitor = original
    assert reconcile(api, body, parse(body)) == "unchanged"
    assert len(api.monitors) == 1


def test_ambiguous_delete_response_recovers(api, body):
    reconcile(api, body, parse(body))
    original = api.delete_monitor

    def commit_then_timeout(id_):
        original(id_)
        raise TimeoutError

    api.delete_monitor = commit_then_timeout
    with pytest.raises(TimeoutError):
        reconcile(api, body, None, deleting=True)
    assert reconcile(api, body, None, deleting=True) == "absent"


def test_unrelated_monitor_fields_are_preserved(api, body):
    reconcile(api, body, parse(body))
    api.monitors[0]["custom_setting"] = "keep"
    cfg = parse(body)
    cfg["interval"] = 120
    reconcile(api, body, cfg)
    assert api.monitors[0]["custom_setting"] == "keep"


def test_multiple_resources_are_independent(api, body):
    for index in range(8):
        other = copy.deepcopy(body)
        other["metadata"].update(name=f"app-{index}", uid=f"uid-{index}")
        reconcile(api, other, parse(other))
    assert len(api.monitors) == 8
    assert len({m["description"] for m in api.monitors}) == 8


def test_ambiguous_legacy_adoption_never_mutates(api, body):
    api.monitors = [dict(id=i, name=str(i), description="k8s:test/app") for i in (1, 2)]
    body["metadata"]["annotations"]["uptime-kuma.io/adopt-legacy"] = "true"
    with pytest.raises(OwnershipConflict):
        reconcile(api, body, parse(body))
    assert not api.writes
    assert len(api.monitors) == 2


def test_existing_owned_duplicate_name_does_not_modify_the_other_monitor(api, body):
    cfg = parse(body)
    reconcile(api, body, cfg)
    owned = api.monitors[0]
    foreign = dict(owned, id=99, description="legacy duplicate")
    api.monitors.append(foreign)
    assert reconcile(api, body, cfg) == "unchanged"
    cfg["interval"] = 120
    assert reconcile(api, body, cfg) == "updated"
    assert api.get_monitor(owned["id"])["interval"] == 120
    assert api.get_monitor(99)["interval"] == 60
