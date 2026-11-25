from types import SimpleNamespace

from app.api.routers import operations
from app.policies.audit import AuditStore


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

def test_interrupted_stress_runs_are_recovered_and_audited(tmp_path):
    path = tmp_path / "operations.sqlite3"
    with operations._connect(path) as db:
        db.execute(
            "INSERT INTO stress_runs(id,status,max_users,users_started,started_at) "
            "VALUES(?,?,?,?,?)",
            ("run-123", "running", 4, 2, "2026-01-01T00:00:00+00:00"),
        )
    audit = AuditStore(tmp_path / "policy.sqlite3")

    recovered = operations.recover_interrupted_stress_runs(path, audit)

    with operations._connect(path) as db:
        row = db.execute("SELECT status, failure FROM stress_runs WHERE id='run-123'").fetchone()
    assert recovered == 1
    assert row["status"] == "interrupted"
    assert "API restarted" in row["failure"]
    entries = audit.list_audit()
    assert entries[0]["action"] == "stress_test_interrupted"
    assert entries[0]["series_id"] == "stress:run-123"
