import pytest
from fastapi.testclient import TestClient

from app.core.domain import METRIC_COLUMNS, HealthThresholds, compute_unhealthy
from app.core.exceptions import DataGenerationError
from data.synthetic.generator import FAULT_TYPES
from demo_app.main import create_demo_app
from demo_app.simulator import LiveSimulator


def run(sim, n):
    import pandas as pd
    return pd.DataFrame([sim.step() for _ in range(n)])


def test_baseline_is_healthy_and_in_range():
    df = run(LiveSimulator(seed=1), 400)
    assert list(df.columns) == list(METRIC_COLUMNS) and not df.isna().any().any()
    assert not compute_unhealthy(df, HealthThresholds()).any()
    assert df["cpu_pct"].between(0, 100).all() and df["error_rate"].between(0, 1).all()


def test_deterministic_for_seed():
    assert run(LiveSimulator(seed=3), 50).equals(run(LiveSimulator(seed=3), 50))


@pytest.mark.parametrize("kind", FAULT_TYPES)
def test_each_fault_drives_service_unhealthy_and_clear_recovers(kind):
    sim = LiveSimulator(seed=2)
    run(sim, 30)
    sim.inject(kind, ramp_minutes=40)
    df = run(sim, 60)
    assert compute_unhealthy(df, HealthThresholds())[-5:].all()  # stays failing after the ramp
    assert sim.state()["fault"] == kind
    sim.clear()
    after = run(sim, 10)
    assert not compute_unhealthy(after, HealthThresholds()).any()


def test_simulator_validation():
    sim = LiveSimulator()
    with pytest.raises(DataGenerationError):
        sim.inject("nope")
    with pytest.raises(DataGenerationError):
        sim.inject("memory_leak", ramp_minutes=1)


def test_metrics_endpoint_and_manual_ticks():
    app = create_demo_app(sim_minute_seconds=999, autostart=False, admin_token="t")
    with TestClient(app) as c:
        text = c.get("/metrics").text
        for m in METRIC_COLUMNS:
            assert f"demo_{m} " in text
        assert "SIMULATED" in text and 'demo_fault_active{fault_type="memory_leak"} 0.0' in text
        app.state.publish()
        assert "demo_sim_minutes_total 2.0" in c.get("/metrics").text
        assert c.get("/healthz").json() == {"status": "ok"}


def test_fault_endpoints_are_protected_and_work():
    app = create_demo_app(sim_minute_seconds=999, autostart=False, admin_token="secret")
    with TestClient(app) as c:
        assert c.post("/fault", json={"fault_type": "memory_leak"}).status_code == 401
        wrong = c.post("/fault", json={"fault_type": "memory_leak"}, headers={"X-Admin-Token": "x"})
        assert wrong.status_code == 401
        h = {"X-Admin-Token": "secret"}
        r = c.post("/fault", json={"fault_type": "memory_leak", "ramp_minutes": 10}, headers=h)
        assert r.status_code == 200 and r.json()["fault"] == "memory_leak"
        for _ in range(12):
            app.state.publish()
        assert 'demo_fault_active{fault_type="memory_leak"} 1.0' in c.get("/metrics").text
        assert c.post("/fault", json={"fault_type": "bogus"}, headers=h).status_code == 422
        assert c.post("/fault/clear", headers=h).json()["fault"] is None


def test_fault_injection_disabled_without_token():
    with TestClient(create_demo_app(sim_minute_seconds=999, autostart=False, admin_token="")) as c:
        assert c.post("/fault", json={"fault_type": "memory_leak"}).status_code == 403


def test_work_endpoint_follows_simulated_error_rate():
    app = create_demo_app(sim_minute_seconds=999, autostart=False, admin_token="t")
    with TestClient(app) as c:
        codes = [c.get("/work").status_code for _ in range(60)]
        assert set(codes) <= {200, 500} and codes.count(200) > 50  # healthy: ~0.4 % errors
        assert 'demo_http_requests_total{status="200"}' in c.get("/metrics").text
