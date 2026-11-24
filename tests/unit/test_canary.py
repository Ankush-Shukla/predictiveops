from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.policies.models import PolicyConfig, RemediationTarget
from app.services.canary import CanaryService, assess
from app.services.canary_models import CanaryConfig

THR = 0.2
T0 = pd.Timestamp("2025-01-01", tz="UTC")
CFG = CanaryConfig(min_observation_minutes=20)


def ser(values, start=0):
    return pd.Series(np.asarray(values, dtype=float),
                     index=pd.date_range(T0 + pd.Timedelta(minutes=start), periods=len(values), freq="min"))


def test_config_validation():
    with pytest.raises(ValidationError):
        CanaryConfig(promote_alert_fraction=0.5, rollback_alert_fraction=0.3)
    with pytest.raises(ValidationError):
        CanaryConfig(min_observation_minutes=60, max_observation_minutes=30)


def test_hold_until_minimum_observation():
    a = assess(ser([0.01] * 19), ser([0.01] * 19), THR, CFG)
    assert a.decision == "hold" and a.observed_minutes == 19 and "19 of 20" in a.reasons[0]
    assert assess(ser([0.01] * 20), ser([0.01] * 20), THR, CFG).decision == "promote"


def test_never_promotes_or_rolls_back_before_minimum_even_if_terrible():
    a = assess(ser([0.01] * 10), ser([0.99] * 10), THR, CFG)
    assert a.decision == "hold"


def test_promote_when_candidate_matches_baseline():
    a = assess(ser([0.02] * 30), ser([0.03] * 30), THR, CFG)
    assert a.decision == "promote" and a.candidate.alert_fraction == 0


def test_rollback_when_clearly_worse_than_baseline():
    cand = [0.05] * 10 + [0.6] * 20  # 67 % of minutes alerting, long high run
    a = assess(ser([0.02] * 30), ser(cand), THR, CFG)
    assert a.decision == "rollback"


def test_rollback_on_alert_fraction_and_margin_without_hard_run():
    cand = ([0.5, 0.05] * 15)  # 50 % alerting but never sustained at >= 80 %
    a = assess(ser([0.02] * 30), ser(cand), THR, CFG)
    assert a.decision == "rollback" and a.candidate.longest_high_run == 0


def test_not_attributable_when_baseline_equally_bad():
    both = [0.9] * 30
    a = assess(ser(both), ser(both), THR, CFG)
    assert a.decision == "hold" and any("not attributable" in r for r in a.reasons)


def test_mild_degradation_is_inconclusive_hold_not_promote():
    cand = [0.1] * 25 + [0.3] * 5  # 17 % alerting: above promote limit, below rollback limit
    a = assess(ser([0.02] * 30), ser(cand), THR, CFG)
    assert a.decision == "hold" and "inconclusive" in a.reasons[-1]


def test_observation_limit_asks_for_human():
    cfg = CanaryConfig(min_observation_minutes=10, max_observation_minutes=20)
    a = assess(ser([0.02] * 25), ser([0.1] * 20 + [0.3] * 5), THR, cfg)
    assert a.decision == "hold" and any("human" in r for r in a.reasons)


def test_hard_fail_run_rolls_back_even_with_low_fraction():
    cfg = CanaryConfig(min_observation_minutes=20, rollback_alert_fraction=0.9, hard_fail_minutes=5)
    cand = [0.02] * 40 + [0.9] * 6  # 13 % alerting but a 6-minute near-certain run
    a = assess(ser([0.02] * 46), ser(cand), THR, cfg)
    assert a.decision == "rollback" and "consecutive" in a.reasons[-1]


def test_only_overlapping_timestamps_are_compared():
    a = assess(ser([0.02] * 30, start=100), ser([0.02] * 30, start=0), THR, CFG)  # no overlap
    assert a.decision == "hold" and a.observed_minutes == 0
    partial = assess(ser([0.02] * 30, start=10), ser([0.02] * 30, start=0), THR, CFG)  # 20 overlap
    assert partial.observed_minutes == 20 and partial.decision == "promote"


def test_missing_baseline_holds_unless_optional():
    assert assess(None, ser([0.01] * 30), THR, CFG).decision == "hold"
    opt = CanaryConfig(min_observation_minutes=20, require_baseline=False)
    assert assess(None, ser([0.01] * 30), THR, opt).decision == "promote"
    assert assess(None, ser([0.9] * 30), THR, opt).decision == "rollback"


# ---- service: rollback goes through the shared gates and audit ----
class StubEvaluator:
    """Returns a scripted assessment; also exposes what CanaryService needs."""

    def __init__(self, decision):
        class P:
            version = "v-test"
        self.predictor, self.config, self._d = P(), CFG, decision

    def evaluate(self, baseline, candidate):
        from app.services.canary_models import CanaryAssessment
        return CanaryAssessment(decision=self._d, reasons=["scripted"], observed_minutes=30, threshold=THR)


def frame(n=3):
    return pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=n, freq="min", tz="UTC")})


def make(decision, *, enabled=True, dry_run=True, targets=None, executor=None, store=None):
    from app.policies.actions import NoopExecutor
    store = store or AuditStore()
    eng = PolicyEngine(PolicyConfig(remediation_enabled=enabled, dry_run=dry_run, cooldown_seconds=900),
                       store, executor or NoopExecutor())
    tg = {"shop": RemediationTarget(container="shop-canary", action="rollback_canary")} \
        if targets is None else targets
    return CanaryService(StubEvaluator(decision), eng, store, tg), eng, store


def test_promote_and_hold_never_touch_remediation():
    for d in ("promote", "hold"):
        svc, eng, store = make(d, dry_run=False)
        r = svc.run("shop", None, frame())
        assert r["rollback"] is None and eng.executor.calls == [] and store.list_audit() == []
        assert store.get_canary_run(r["id"])["decision"] == d


def test_rollback_dry_run_records_but_does_not_execute():
    svc, eng, store = make("rollback")
    r = svc.run("shop", None, frame())
    assert r["rollback"]["outcome"] == "dry_run" and r["rollback"]["container"] == "shop-canary"
    assert eng.executor.calls == []
    a = store.list_audit()[0]
    assert a["action"] == "rollback_canary" and a["target"] == "shop-canary" and a["outcome"] == "dry_run"


def test_rollback_live_executes_once_then_cooldown():
    svc, eng, store = make("rollback", dry_run=False)
    assert svc.run("shop", None, frame())["rollback"]["outcome"] == "executed"
    assert eng.executor.calls == [("rollback_canary", "shop-canary")]
    assert svc.run("shop", None, frame())["rollback"]["outcome"] == "skipped_cooldown"
    assert len(eng.executor.calls) == 1


def test_rollback_blocked_without_target_or_when_disabled_or_wrong_action():
    svc0 = make("rollback", targets={})[0]
    assert svc0.run("shop", None, frame())["rollback"]["outcome"] == "blocked_no_target"
    svc, eng, _ = make("rollback", enabled=False, dry_run=False)
    r = svc.run("shop", None, frame())
    assert r["rollback"]["outcome"] == "blocked_disabled" and eng.executor.calls == []
    wrong = {"shop": RemediationTarget(container="shop-1", action="restart_container")}
    svc, eng, store = make("rollback", dry_run=False, targets=wrong)
    r = svc.run("shop", None, frame())
    assert r["rollback"]["outcome"] == "blocked_not_allowlisted" and eng.executor.calls == []
    assert store.list_audit()[0]["outcome"] == "blocked_not_allowlisted"


def test_unconfigured_service_cannot_trigger_other_targets():
    svc, eng, _ = make("rollback", dry_run=False)
    r = svc.run("some-other-service", None, frame())
    assert r["rollback"]["outcome"] == "blocked_no_target" and r["rollback"]["container"] is None
    assert eng.executor.calls == []


def test_event_time_uses_candidate_timestamps():
    svc, eng, store = make("rollback", dry_run=False)
    svc.run("shop", None, frame())
    ts = datetime.fromisoformat(store.list_audit()[0]["ts"])
    assert ts - datetime(2025, 1, 1, tzinfo=UTC) <= timedelta(minutes=5)
