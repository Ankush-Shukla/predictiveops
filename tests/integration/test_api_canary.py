import copy

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import Settings
from app.models.predictor import Predictor
from app.policies.models import RemediationTarget
from data.synthetic.generator import GeneratorConfig, generate_dataset
from data.synthetic.scenarios import generate_scenario
from training.gates import GateConfig
from training.train import TrainConfig, run_training

METRICS = ["cpu_pct", "memory_pct", "disk_io_util_pct", "network_rx_mbps", "network_tx_mbps",
           "request_rate_rps", "latency_p95_ms", "error_rate"]
BASE = dict(cpu_pct=20, memory_pct=50, disk_io_util_pct=10, network_rx_mbps=4, network_tx_mbps=6,
            request_rate_rps=90, latency_p95_ms=110, error_rate=0.004)
TARGETS = {"shop": RemediationTarget(container="shop-canary", action="rollback_canary")}


@pytest.fixture(scope="module")
def predictor(tmp_path_factory):
    df = generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))
    root = tmp_path_factory.mktemp("m")
    res = run_training(df, TrainConfig(model_type="logreg", model_root=root, use_mlflow=False,
                                       max_fpr_for_threshold=0.2, gates=GateConfig(min_f1=0.0)))
    return Predictor(res.bundle, res.version)


def memory_rule(base: Predictor) -> Predictor:
    """Scripted model: risk 0.9 whenever memory >= 70 else 0.02 (tests canary wiring, not model quality)."""
    p = copy.copy(base)
    p.bundle = copy.copy(base.bundle)
    p.bundle.threshold = 0.2
    need = base.required_history

    def predict_recent(window: pd.DataFrame, n: int):
        w = window.sort_values("timestamp").reset_index(drop=True).iloc[need - 1:]
        w = w.iloc[-n:] if n else w.iloc[:0]
        return [p._result(0.9 if m >= 70 else 0.02, t) for t, m in zip(w["timestamp"], w["memory_pct"],
                                                                         strict=True)]

    p.predict_recent = predict_recent  # type: ignore[method-assign]
    return p


def points(n, mem, start=0):
    ts = pd.date_range("2025-01-01", periods=start + n, freq="min", tz="UTC")[start:]
    return [{"timestamp": t.isoformat(), **{**BASE, "memory_pct": mem}} for t in ts]


def client(pred, tmp_path, **kw):
    s = Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "a.sqlite", **kw)
    return TestClient(create_app(s, pred, load_model=False))


def test_rollback_is_gated_audited_and_persisted(predictor, tmp_path):
    with client(memory_rule(predictor), tmp_path, remediation_enabled=True, canary_targets=TARGETS) as c:
        r = c.post("/v1/canary/evaluate", json={"service": "shop", "baseline": points(60, 50),
                                                "candidate": points(60, 95)})
        assert r.status_code == 200
        j = r.json()
        assert j["assessment"]["decision"] == "rollback"
        assert j["rollback"] == {"outcome": "dry_run", "container": "shop-canary",
                                 "detail": "dry-run: action NOT executed"}
        a = c.get("/v1/audit").json()[0]
        assert (a["action"], a["target"], a["outcome"]) == ("rollback_canary", "shop-canary", "dry_run")
        assert c.get(f"/v1/canary/{j['id']}").json()["assessment"]["decision"] == "rollback"
        assert c.get("/v1/canary").json()[0]["decision"] == "rollback"
        assert c.get("/v1/canary/9999").status_code == 404
        assert 'predictiveops_canary_evaluations_total{decision="rollback"} 1.0' in c.get("/metrics").text


def test_promote_and_hold_do_not_touch_audit(predictor, tmp_path):
    with client(memory_rule(predictor), tmp_path, remediation_enabled=True, canary_targets=TARGETS) as c:
        ok = c.post("/v1/canary/evaluate", json={"service": "shop", "baseline": points(60, 50),
                                                 "candidate": points(60, 50)}).json()
        assert ok["assessment"]["decision"] == "promote" and ok["rollback"] is None
        short = c.post("/v1/canary/evaluate", json={"service": "shop", "baseline": points(60, 50),
                                                    "candidate": points(35, 95)}).json()
        assert short["assessment"]["decision"] == "hold" and short["assessment"]["observed_minutes"] == 6
        nobase = c.post("/v1/canary/evaluate", json={"service": "shop", "candidate": points(60, 50)}).json()
        assert nobase["assessment"]["decision"] == "hold"
        assert c.get("/v1/audit").json() == []


def test_request_cannot_name_a_container_or_unconfigured_target(predictor, tmp_path):
    with client(memory_rule(predictor), tmp_path, remediation_enabled=True, canary_targets=TARGETS) as c:
        body = {"service": "shop", "baseline": points(60, 50), "candidate": points(60, 95),
                "container": "postgres", "action": "restart_container"}
        j = c.post("/v1/canary/evaluate", json=body).json()
        assert j["rollback"]["container"] == "shop-canary"  # extra fields are ignored
        other = c.post("/v1/canary/evaluate", json={**body, "service": "payments"}).json()
        assert other["rollback"]["outcome"] == "blocked_no_target" and other["rollback"]["container"] is None
        bad = c.post("/v1/canary/evaluate", json={**body, "service": "x; rm -rf /"})
        assert bad.status_code == 422


def test_rollback_blocked_by_default_config(predictor, tmp_path):
    with client(memory_rule(predictor), tmp_path, canary_targets=TARGETS) as c:  # remediation disabled
        j = c.post("/v1/canary/evaluate", json={"service": "shop", "baseline": points(60, 50),
                                                "candidate": points(60, 95)}).json()
        assert j["assessment"]["decision"] == "rollback" and j["rollback"]["outcome"] == "blocked_disabled"


def test_api_key_protects_evaluate(predictor, tmp_path):
    with client(memory_rule(predictor), tmp_path, api_key="k", canary_targets=TARGETS) as c:
        body = {"service": "shop", "baseline": points(60, 50), "candidate": points(60, 50)}
        assert c.post("/v1/canary/evaluate", json=body).status_code == 401
        assert c.post("/v1/canary/evaluate", json=body, headers={"X-API-Key": "k"}).status_code == 200
        assert c.get("/v1/canary").status_code == 200  # reads stay open


def test_validation_and_real_model_structure(predictor, tmp_path):
    with client(predictor, tmp_path) as c:
        assert c.post("/v1/canary/evaluate", json={"service": "s", "candidate": []}).status_code == 422
        gappy = points(60, 50)
        del gappy[40:45]
        r = c.post("/v1/canary/evaluate", json={"service": "s", "candidate": gappy})
        assert r.status_code == 422 and "regular" in r.json()["detail"]
        scn = generate_scenario("memory_leak", minutes=180, fault_start=10, ramp_minutes=45, seed=3,
                                min_fault_start=5)
        pts = [{"timestamp": r.timestamp.isoformat(), **{m: float(getattr(r, m)) for m in METRICS}}
               for r in scn.itertuples()]
        base = generate_scenario("none", minutes=180, seed=3)
        bpts = [{"timestamp": r.timestamp.isoformat(), **{m: float(getattr(r, m)) for m in METRICS}}
                for r in base.itertuples()]
        j = c.post("/v1/canary/evaluate", json={"service": "s", "baseline": bpts, "candidate": pts}).json()
        assert j["assessment"]["decision"] in ("promote", "hold", "rollback")
        assert j["assessment"]["observed_minutes"] == 180 - predictor.required_history + 1


def test_simulate_rollback_promote_and_isolation(predictor, tmp_path):
    with client(memory_rule(predictor), tmp_path) as c:
        bad = c.post("/v1/canary/simulate", json={"candidate_fault": "memory_leak", "seed": 5}).json()
        f = bad["final"]
        assert f["decision"] == "rollback" and 48 <= f["minute"] <= 75
        assert f["rollback_outcome"] == "dry_run" and f["rollback_container"] == "demo-canary"
        assert bad["checkpoints"][-1]["decision"] == "rollback"
        assert all(x["decision"] == "hold" for x in bad["checkpoints"][:-1])
        assert len(bad["baseline_probability"]) == len(bad["candidate_probability"]) == bad["minutes"]
        good = c.post("/v1/canary/simulate", json={"candidate_fault": "none", "seed": 5}).json()
        assert good["final"]["decision"] == "promote" and good["final"]["rollback_outcome"] is None
        assert c.get("/v1/audit").json() == [] and c.get("/v1/canary").json() == []  # nothing persisted
        invalid = c.post("/v1/canary/simulate", json={"candidate_fault": "memory_leak", "minutes": 90,
                                                      "fault_start": 80, "ramp_minutes": 60})
        assert invalid.status_code == 422 and invalid.json()["error"] == "invalid_scenario"


def test_known_limitation_fault_after_decision_is_not_caught(predictor, tmp_path):
    """Documented limitation: a regression that starts AFTER the observation window is promoted."""
    with client(memory_rule(predictor), tmp_path) as c:
        j = c.post("/v1/canary/simulate", json={"candidate_fault": "memory_leak", "fault_start": 120,
                                                "minutes": 300, "ramp_minutes": 45}).json()
        assert j["final"]["decision"] == "promote" and j["final"]["minute"] < 120
