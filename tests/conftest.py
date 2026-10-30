import copy
import pytest


@pytest.fixture
def body():
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "app",
            "namespace": "test",
            "uid": "uid-1",
            "generation": 1,
            "annotations": {"uptime-kuma.io/enabled": "true", "uptime-kuma.io/url": "https://example.com"},
        },
        "spec": {},
    }


class FakeKuma:
    """Stateful remote model; asserts behavior, not call wiring."""

    def __init__(self):
        self.monitors = []
        self.notifications = []
        self.writes = []
        self.next_id = 1
        self.fail = False

    def get_monitors(self):
        if self.fail:
            raise TimeoutError
        return copy.deepcopy(self.monitors)

    def get_monitor(self, id_):
        return copy.deepcopy(next(m for m in self.monitors if m["id"] == id_))

    def get_notifications(self):
        return copy.deepcopy(self.notifications)

    def add_monitor(self, **kwargs):
        id_ = self.next_id
        self.next_id += 1
        self.monitors.append(dict(kwargs, id=id_))
        self.writes.append(("create", id_))
        return {"monitorID": id_}

    def edit_monitor(self, id_, **kwargs):
        next(m for m in self.monitors if m["id"] == id_).update(kwargs)
        self.writes.append(("edit", id_))

    def delete_monitor(self, id_):
        self.monitors = [m for m in self.monitors if m["id"] != id_]
        self.writes.append(("delete", id_))


@pytest.fixture
def api():
    return FakeKuma()
