import copy
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import Settings
from app.models.predictor import Predictor
from app.policies.models import RemediationTarget
from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

T0 = datetime(2025, 1, 1, tzinfo=UTC)
METRICS = dict(cpu_pct=20, memory_pct=50, disk_io_util_pct=10, network_rx_mbps=4, network_tx_mbps=6,
               request_rate_rps=90, latency_p95_ms=110, error_rate=0.004)


@pytest.fixture(scope="module")
def predictor(tmp_path_factory):
    df = generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))
    root = tmp_path_factory.mktemp("m")
    res = run_training(df, TrainConfig(model_type="logreg", model_root=root, use_mlflow=False,
                                       max_fpr_for_threshold=0.2, gates=GateConfig(min_f1=0.0)))
    return Predictor(res.bundle, res.version)


def scripted(base: Predictor, probs: list[float]) -> Predictor:
    """Predictor whose per-minute probability follows ``probs`` (wiring tests, not model quality)."""
    p = copy.copy(base)

    def predict_recent(window: pd.DataFrame, n: int):
        ts = window["timestamp"].iloc[-n:]
        return [p._result(probs[int((t - pd.Timestamp(T0)) / pd.Timedelta(minutes=1))], t) for t in ts]

    p.predict_recent = predict_recent  # type: ignore[method-assign]
    return p


def pts(start: int, n: int):
    return [{"timestamp": (T0 + timedelta(minutes=start + i)).isoformat(), **METRICS} for i in range(n)]


def make_client(predictor, tmp_path, **kw):
    s = Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "audit.sqlite", **kw)
    return TestClient(create_app(s, predictor, load_model=False))


def feed(c, series, total, batch=20):
    last = None
    for start in range(0, total, batch):
        last = c.post("/v1/metrics/ingest", json={"series_id": series, "samples": pts(start, batch)}).json()
    return last


def test_ingest_drives_alerts_and_dry_run_audit(predictor, tmp_path):
    probs = [0.05] * 40 + [0.9] * 12 + [0.05] * 28
    targets = {"svc": RemediationTarget(container="app-1", action="restart_container")}
    with make_client(scripted(predictor, probs), tmp_path, remediation_enabled=True,
                     remediation_targets=targets) as c:
        last = feed(c, "svc", 80)
        assert last["scored_samples"] > 0
        alerts = c.get("/v1/alerts").json()
        assert {"alert", "remediate", "resolved"} <= {a["level"] for a in alerts}
        audit = c.get("/v1/audit").json()
        # risk stayed high long enough for a second attempt, which the 15-minute cooldown blocked
        assert [a["outcome"] for a in audit] == ["skipped_cooldown", "dry_run"]
        a = audit[-1]
        assert (a["outcome"], a["dry_run"], a["target"], a["action"]) == ("dry_run", True, "app-1",
                                                                         "restart_container")
        assert c.get("/v1/audit", params={"series_id": "other"}).json() == []
        pol = c.get("/v1/policy").json()
        assert pol["config"]["dry_run"] is True and "svc" in pol["series"]
        assert c.get("/v1/series/svc").json()["policy_level"] in ("none", "warn", "alert", "remediate")
        text = c.get("/metrics").text
        assert 'predictiveops_policy_level{series_id="svc"}' in text
        assert 'predictiveops_alerts_total{level="alert"}' in text
        assert 'predictiveops_remediation_decisions_total{outcome="dry_run"} 1.0' in text
        assert 'predictiveops_remediation_decisions_total{outcome="skipped_cooldown"}' in text


def test_remediation_blocked_by_default(predictor, tmp_path):
    probs = [0.05] * 40 + [0.9] * 15 + [0.05] * 25
    with make_client(scripted(predictor, probs), tmp_path) as c:  # defaults: disabled, no targets
        feed(c, "svc", 80)
        audit = c.get("/v1/audit").json()
        assert [a["outcome"] for a in audit] == ["blocked_no_target"]
        assert c.get("/v1/policy").json()["config"]["remediation_enabled"] is False


def test_audit_and_alerts_persist_across_restart(predictor, tmp_path):
    probs = [0.05] * 40 + [0.9] * 15 + [0.05] * 25
    with make_client(scripted(predictor, probs), tmp_path) as c:
        feed(c, "svc", 80)
    with make_client(None, tmp_path, ) as c2:
        assert len(c2.get("/v1/audit").json()) == 1 and len(c2.get("/v1/alerts").json()) >= 2


def test_real_model_ingest_returns_decision(predictor, tmp_path):
    df = generate_dataset(GeneratorConfig(days=2, n_series=1, seed=5))
    rows = [{"timestamp": r.timestamp.isoformat(), **{k: float(getattr(r, k)) for k in METRICS}}
            for r in df.iloc[:90].itertuples()]
    with make_client(predictor, tmp_path) as c:
        j = c.post("/v1/metrics/ingest", json={"series_id": "real", "samples": rows}).json()
        assert j["scored_samples"] == 90 - predictor.required_history + 1
        assert j["decision"]["level"] in ("none", "warn", "alert", "remediate")
        assert j["prediction"]["data_source"] == "synthetic"


def test_policy_preview_is_side_effect_free(predictor, tmp_path):
    with make_client(predictor, tmp_path) as c:
        r = c.post("/v1/policy/evaluate", json={"probabilities": [0.9] * 6, "threshold": 0.2})
        assert r.status_code == 200
        d = r.json()
        assert [x["level"] for x in d][:5] == ["warn", "warn", "alert", "alert", "remediate"]
        assert d[4]["action_outcome"] == "blocked_no_target"  # honest: default config has no targets
        assert c.get("/v1/audit").json() == [] and c.get("/v1/alerts").json() == []
        bad = c.post("/v1/policy/evaluate", json={"probabilities": [], "threshold": 0.2})
        assert bad.status_code == 422


def test_simulate_includes_policy_replay(predictor, tmp_path):
    with make_client(predictor, tmp_path) as c:
        j = c.post("/v1/simulate", json={"fault_type": "memory_leak", "seed": 3}).json()
        n = j["minutes"]
        assert len(j["policy_level"]) == n and j["policy_level"][0] is None
        ps = j["policy_summary"]
        assert ps["sustain_alert"] == 3 and ps["sustain_remediate"] == 5
        assert ps["alert_minutes"] == sum(x in ("alert", "remediate") for x in j["policy_level"])
        assert c.get("/v1/audit").json() == []  # replay never touches the real audit log
        h = c.post("/v1/simulate", json={"fault_type": "none"}).json()
        assert h["policy_summary"]["alert_minutes"] == 0 or h["summary"]["alert_minutes"] > 0
