from types import SimpleNamespace

from app.api.routers import operations


class FakeContainer:
    def __init__(self, name, labels):
        self.name = name
        self.labels = labels
        self.id = name * 64
        self.short_id = name * 12
        self.status = "running"
        self.attrs = {"State": {"Status": "running", "ExitCode": 0}}

    def reload(self):
        return None

    def remove(self, force=False):
        self.removed = force


class FakeClient:
    def __init__(self, containers):
        self.containers = SimpleNamespace(list=lambda **kwargs: containers)


def test_stress_workers_are_hidden_from_managed_inventory(monkeypatch):
    compose = {"com.docker.compose.project": operations.PROJECT}
    normal = FakeContainer("a", {**compose, "com.docker.compose.service": "api"})
    stress = FakeContainer("b", {**compose, "predictiveops.role": "stress"})
    monkeypatch.setattr(operations, "_client", lambda: FakeClient([normal, stress]))

    result = operations.managed_containers()

    assert result["available"] is True
    assert [row["name"] for row in result["containers"]] == ["a"]


def test_startup_cleanup_removes_only_labeled_stress_workers(monkeypatch):
    compose = {"com.docker.compose.project": operations.PROJECT}
    stress = FakeContainer("b", {**compose, "predictiveops.role": "stress"})
    monkeypatch.setattr(operations, "_client", lambda: FakeClient([stress]))

    removed = operations.cleanup_orphaned_stress_containers()

    assert removed == 1
    assert stress.removed is True
