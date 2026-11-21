from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.policies.actions import ActionNotAllowedError, DockerExecutor, NoopExecutor
from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.policies.models import PolicyConfig, RemediationTarget

T0 = datetime(2025, 1, 1, tzinfo=UTC)
THR = 0.2


def tgt(action="restart_container"):
    return {"s": RemediationTarget(container="app-1", action=action)}


def cfg(**kw):
    base = dict(sustain_alert=3, sustain_remediate=5, remediate_threshold=0.8, remediation_enabled=True,
                dry_run=True, cooldown_seconds=900, max_actions_per_hour=4, targets=tgt())
    base.update(kw)
    return PolicyConfig(**base)


def run(engine, probs, start=0, sid="s"):
    return [engine.evaluate(sid, p, THR, T0 + timedelta(minutes=start + i), "v1")
            for i, p in enumerate(probs)]


def outcomes(decisions):
    return [d.action_outcome for d in decisions if d.action_outcome]


def test_config_validation():
    with pytest.raises(ValidationError):
        PolicyConfig(sustain_alert=5, sustain_remediate=3)
    with pytest.raises(ValidationError):
        RemediationTarget(container="x; rm -rf /")
    with pytest.raises(ValidationError):
        RemediationTarget(container="ok", action="exec_shell")  # type: ignore[arg-type]


def test_levels_and_alert_events():
    e = PolicyEngine(cfg())
    d = run(e, [0.1, 0.3, 0.3, 0.3, 0.3, 0.05])
    assert [x.level for x in d] == ["none", "warn", "warn", "alert", "alert", "none"]
    kinds = [a["level"] for a in e.store.list_alerts()]
    assert kinds == ["resolved", "alert"]  # newest first; alert recorded once


def test_transient_spike_never_alerts():
    e = PolicyEngine(cfg())
    d = run(e, [0.05, 0.9, 0.9, 0.05, 0.9, 0.9, 0.05])
    assert all(x.level in ("none", "warn") for x in d)
    assert e.store.list_alerts() == []


def test_dry_run_remediation_is_audited_and_resets():
    e = PolicyEngine(cfg(), executor=NoopExecutor())
    d = run(e, [0.9] * 5)
    assert d[-1].level == "remediate" and d[-1].action_outcome == "dry_run"
    assert e.executor.calls == []  # dry-run never executes
    a = e.store.list_audit()
    assert len(a) == 1 and a[0]["outcome"] == "dry_run" and a[0]["dry_run"] is True
    assert a[0]["target"] == "app-1" and a[0]["action"] == "restart_container"
    assert e.snapshot()["s"]["level"] == "none"  # counters reset after an action


def test_blocked_outcomes_and_audit_dedupe():
    e = PolicyEngine(cfg(remediation_enabled=False))
    d = run(e, [0.9] * 9)
    assert d[-1].action_outcome == "blocked_disabled"
    assert len(e.store.list_audit()) == 1  # not one row per minute
    e2 = PolicyEngine(cfg(targets={}))
    assert run(e2, [0.9] * 5)[-1].action_outcome == "blocked_no_target"
    e3 = PolicyEngine(cfg(allowed_actions=("restart_container",), targets=tgt("rollback_canary")))
    assert run(e3, [0.9] * 5)[-1].action_outcome == "blocked_not_allowlisted"


def test_real_execution_cooldown_and_rate_limit():
    ex = NoopExecutor()
    e = PolicyEngine(cfg(dry_run=False), executor=ex)
    d = run(e, [0.9] * 5)
    assert d[-1].action_outcome == "executed" and ex.calls == [("restart_container", "app-1")]
    d2 = run(e, [0.9] * 5, start=5)  # 5 minutes later: inside the 15-min cooldown
    assert d2[-1].action_outcome == "skipped_cooldown" and len(ex.calls) == 1
    d3 = run(e, [0.9] * 5, start=30)  # cooldown over; risk still high -> acts on the first minute
    assert outcomes(d3) == ["executed"] and len(ex.calls) == 2

    ex2 = NoopExecutor()
    e2 = PolicyEngine(cfg(dry_run=False, cooldown_seconds=0, max_actions_per_hour=1), executor=ex2)
    run(e2, [0.9] * 5)
    assert run(e2, [0.9] * 5, start=5)[-1].action_outcome == "skipped_rate_limit"
    assert len(ex2.calls) == 1
    assert outcomes(run(e2, [0.9] * 5, start=90)) == ["executed"]  # window slid past 1 hour


def test_dry_run_counts_against_limits():
    e = PolicyEngine(cfg(dry_run=True))
    run(e, [0.9] * 5)
    assert run(e, [0.9] * 5, start=5)[-1].action_outcome == "skipped_cooldown"


def test_cooldown_survives_restart(tmp_path):
    db = tmp_path / "audit.sqlite"
    run(PolicyEngine(cfg(dry_run=False), AuditStore(db), NoopExecutor()), [0.9] * 5)
    ex = NoopExecutor()
    again = PolicyEngine(cfg(dry_run=False), AuditStore(db), ex)  # simulated process restart
    assert run(again, [0.9] * 5, start=6)[-1].action_outcome == "skipped_cooldown" and ex.calls == []


def test_executor_failure_is_contained():
    class Boom:
        def execute(self, action, container):
            raise RuntimeError("docker down")

    e = PolicyEngine(cfg(dry_run=False), executor=Boom())
    d = run(e, [0.9] * 5)
    assert d[-1].action_outcome == "failed"
    assert "docker down" in e.store.list_audit()[0]["reason"]


def test_audit_filters_and_order():
    s = AuditStore()
    for i, sid in enumerate(["a", "b", "a"]):
        s.add_alert(ts=T0 + timedelta(minutes=i), series_id=sid, level="alert", probability=0.5,
                    threshold=0.2, model_version="v", message="m")
    assert [r["series_id"] for r in s.list_alerts()] == ["a", "b", "a"]
    assert len(s.list_alerts(series_id="a")) == 2 and len(s.list_alerts(limit=1)) == 1


class FakeContainer:
    def __init__(self, labels):
        self.labels, self.calls = labels, []

    def restart(self, timeout):
        self.calls.append(("restart", timeout))

    def stop(self, timeout):
        self.calls.append(("stop", timeout))


class FakeClient:
    def __init__(self, containers):
        self.containers = self
        self._c = containers
        self.asked = []

    def get(self, name):
        self.asked.append(name)
        return self._c[name]


def test_docker_executor_safety():
    managed = FakeContainer({"predictiveops.managed": "true"})
    canary = FakeContainer({"predictiveops.managed": "true", "predictiveops.role": "canary"})
    other = FakeContainer({})
    client = FakeClient({"app": managed, "can": canary, "db": other})
    ex = DockerExecutor(client)
    ex.execute("restart_container", "app")
    assert managed.calls == [("restart", 10)]
    with pytest.raises(ActionNotAllowedError):  # unlabelled container (e.g. an unrelated database)
        ex.execute("restart_container", "db")
    assert other.calls == []
    with pytest.raises(ActionNotAllowedError):  # rollback only on canaries
        ex.execute("rollback_canary", "app")
    ex.execute("rollback_canary", "can")
    assert canary.calls == [("stop", 10)]
    for bad_action in ("exec", "rm -rf /", "stop_container"):
        with pytest.raises(ActionNotAllowedError):
            ex.execute(bad_action, "app")
    with pytest.raises(ActionNotAllowedError):
        ex.execute("restart_container", "app; rm -rf /")
    assert "app; rm -rf /" not in client.asked  # invalid names never reach the Docker API
