import copy
import time

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.poller import PrometheusPoller
from app.api.state import ApiState
from app.core.config import Settings
from app.core.domain import METRIC_COLUMNS
from app.models.predictor import Predictor
from app.policies.models import RemediationTarget
from app.services.prometheus_client import PrometheusClient, PrometheusError
from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

T0 = 1_700_000_000.0  # divisible by 5
BASE = dict(cpu_pct=20, memory_pct=50, disk_io_util_pct=10, network_rx_mbps=4, network_tx_mbps=6,
            request_rate_rps=90, latency_p95_ms=110, error_rate=0.004)


def virtual_prometheus(value_fn, calls=None, status=200):
    """MockTransport emulating /api/v1/query_range for the default demo queries."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/-/healthy":
            return httpx.Response(200, text="ok")
        q = req.url.params["query"]
        if calls is not None:
            calls.append(q)
        if status != 200:
            return httpx.Response(status, json={"status": "error", "error": "boom"})
        metric = next(m for m in METRIC_COLUMNS if f"demo_{m}{{" in q)
        start, end, step = (float(req.url.params[k]) for k in ("start", "end", "step"))
        vals, t = [], start
        while t <= end + 1e-6:
            v = value_fn(t, metric)
            if v is not None:
                vals.append([t, str(v)])
            t += step
        result = [{"metric": {}, "values": vals}] if vals else []
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix",
                                                                       "result": result}})
    return httpx.MockTransport(handler)


def healthy(t, m):
    return BASE[m]


def client_for(fn, **kw):
    return PrometheusClient("http://prom:9090", transport=virtual_prometheus(fn, **kw))


# ---------------- client ----------------
def test_fetch_window_builds_aligned_complete_frame_and_sanitises_service():
    calls = []
    c = client_for(healthy, calls=calls)
    df = c.fetch_window("demo-app", end=T0 + 100, steps=10, step_seconds=5)
    assert len(df) == 10 and set(METRIC_COLUMNS) <= set(df.columns)
    assert all('service="demo-app"' in q for q in calls) and len(calls) == 8
    calls.clear()
    for bad in ('x"} or vector(1) #', "a b", "", "../x", "svc}"):
        with pytest.raises(PrometheusError):
            c.fetch_window(bad, end=T0, steps=5, step_seconds=5)
    assert calls == []  # nothing reached Prometheus


def test_rows_missing_any_metric_are_dropped():
    def partial(t, m):
        return None if (m == "cpu_pct" and t < T0 + 20) else BASE[m]
    df = client_for(partial).fetch_window("s", end=T0 + 50, steps=11, step_seconds=5)
    assert df["ts"].min() >= T0 + 20 and len(df) == 7


def test_errors_and_ambiguity_raise_prometheus_error():
    with pytest.raises(PrometheusError, match="boom"):
        client_for(healthy, status=500).fetch_window("s", end=T0, steps=5, step_seconds=5)

    def two_series(req):
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": {}, "values": [[T0, "1"]]}, {"metric": {}, "values": [[T0, "2"]]}]}})
    c = PrometheusClient("http://p", transport=httpx.MockTransport(two_series))
    with pytest.raises(PrometheusError, match="several series"):
        c.query_range("x", T0, T0, 5)

    def down(req):
        raise httpx.ConnectError("refused")
    d = PrometheusClient("http://p", transport=httpx.MockTransport(down))
    assert d.healthy() is False
    with pytest.raises(PrometheusError):
        d.query_range("x", T0, T0, 5)


def test_empty_result_gives_empty_frame():
    df = client_for(lambda t, m: None).fetch_window("s", end=T0 + 50, steps=5, step_seconds=5)
    assert df.empty


def test_sample_clock_is_regular_and_stable_across_polls():
    c = client_for(healthy)
    origin_sample_ts = pd.Timestamp("2026-11-24T19:00:00Z")

    a = PrometheusClient.to_sample_clock(
        c.fetch_window("s", end=T0 + 100, steps=20, step_seconds=5),
        5,
        origin_ts=T0,
        origin_sample_ts=origin_sample_ts,
    )
    b = PrometheusClient.to_sample_clock(
        c.fetch_window("s", end=T0 + 125, steps=20, step_seconds=5),
        5,
        origin_ts=T0,
        origin_sample_ts=origin_sample_ts,
    )

    assert (a["timestamp"].diff().dropna() == pd.Timedelta(minutes=1)).all()
    assert a["timestamp"].max().year == 2026

    shared = set(a["timestamp"]) & set(b["timestamp"])
    assert len(shared) == 15

def test_custom_queries_must_cover_all_metrics():
    with pytest.raises(ValueError):
        PrometheusClient("http://p", queries={"cpu_pct": "x"})


# ---------------- poller ----------------
@pytest.fixture(scope="module")
def predictor(tmp_path_factory):
    df = generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))
    res = run_training(df, TrainConfig(model_type="logreg", model_root=tmp_path_factory.mktemp("m"),
                                       use_mlflow=False, max_fpr_for_threshold=0.2,
                                       gates=GateConfig(min_f1=0.0)))
    return Predictor(res.bundle, res.version)


def memory_rule(base: Predictor) -> Predictor:
    p = copy.copy(base)
    p.bundle = copy.copy(base.bundle)
    p.bundle.threshold = 0.2
    need = base.required_history

    def predict_recent(window, n):
        w = window.sort_values("timestamp").reset_index(drop=True).iloc[need - 1:].iloc[-n:]
        return [p._result(0.9 if m >= 70 else 0.02, t)
                for t, m in zip(w["timestamp"], w["memory_pct"], strict=True)]
    p.predict_recent = predict_recent  # type: ignore[method-assign]
    return p


def make_state(predictor, tmp_path, **kw):
    s = Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "a.sqlite", **kw)
    return ApiState(settings=s, predictor=memory_rule(predictor) if predictor else None)


def poller_for(state, fn, now):
    return PrometheusPoller(state, client_for(fn), ["demo-app"], step_seconds=5, interval_seconds=1,
                            lookback_steps=60, clock=lambda: now[0])


def test_poller_ingests_without_duplicates_and_scores(predictor, tmp_path):
    st = make_state(predictor, tmp_path)
    now = [T0 + 59 * 5]
    pl = poller_for(st, healthy, now)
    pl.poll_once()
    assert pl.status["demo-app"]["error"] is None and pl.status["demo-app"]["samples_ingested"] == 60
    assert len(st.buffers["demo-app"]) == 60 and st.latest["demo-app"] is not None
    now[0] += 25  # five new steps; window overlaps 55 already-seen steps
    pl.poll_once()
    assert pl.status["demo-app"]["samples_ingested"] == 65 and len(st.buffers["demo-app"]) == 65
    pl.poll_once()  # nothing new
    assert pl.status["demo-app"]["samples_ingested"] == 65


def test_poller_drives_alerts_and_dry_run_remediation(predictor, tmp_path):
    targets = {"demo-app": RemediationTarget(container="demo", action="restart_container")}
    st = make_state(predictor, tmp_path, remediation_enabled=True, remediation_targets=targets)
    leak_from = T0 + 59 * 5 + 40 * 5  # memory climbs after 40 healthy steps

    def fn(t, m):
        return 95 if (m == "memory_pct" and t >= leak_from) else BASE[m]
    now = [T0 + 59 * 5]
    pl = poller_for(st, fn, now)
    for _ in range(30):
        pl.poll_once()
        now[0] += 5 * 3
    levels = {a["level"] for a in st.store.list_alerts(50)}
    assert {"alert", "remediate"} <= levels
    assert st.store.list_audit()[-1]["outcome"] == "dry_run"
    assert 'predictiveops_policy_level{series_id="demo-app"}' in __import__(
        "prometheus_client").generate_latest(st.registry).decode()


def test_poller_survives_failures_and_gaps(predictor, tmp_path):
    st = make_state(predictor, tmp_path)
    now = [T0 + 59 * 5]
    pl = poller_for(st, healthy, now)
    pl.poll_once()
    bad = PrometheusPoller(st, client_for(healthy, status=500), ["demo-app"], step_seconds=5,
                           interval_seconds=1, lookback_steps=60, clock=lambda: now[0])
    bad.poll_once()  # must not raise
    assert "boom" in bad.status["demo-app"]["error"] and bad.status["demo-app"]["last_ok"] is None
    gap = PrometheusPoller(st, client_for(lambda t, m: None if T0 + 400 <= t < T0 + 480 else BASE[m]),
                           ["demo-app"], step_seconds=5, interval_seconds=1, lookback_steps=60,
                           clock=lambda: T0 + 59 * 5 + 300)
    gap.poll_once()
    assert len(st.buffers["demo-app"]) < 60 + 60  # the gap cleared history instead of bridging it
    nomodel = make_state(None, tmp_path / "x")
    p2 = poller_for(nomodel, healthy, now)
    p2.poll_once()
    assert "no champion model" in p2.status["demo-app"]["error"]


def test_app_lifecycle_runs_poller_and_exposes_sources(predictor, tmp_path):
    s = Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "a.sqlite",
                 prometheus_enabled=True, prometheus_services="demo-app", prometheus_step_seconds=5,
                 prometheus_poll_seconds=1, prometheus_lookback_steps=60)
    app = create_app(s, memory_rule(predictor), load_model=False,
                     prometheus_client=client_for(lambda t, m: BASE[m]))
    with TestClient(app) as c:
        for _ in range(40):
            src = c.get("/v1/sources").json()["prometheus"]
            if src["services"]["demo-app"]["last_ok"]:
                break
            time.sleep(0.1)
        assert src["enabled"] is True and src["services"]["demo-app"]["samples_ingested"] > 0
        assert c.get("/v1/series/demo-app").json()["buffered"] > 0
    off = create_app(Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "b.sqlite"),
                     None, load_model=False)
    with TestClient(off) as c:
        assert c.get("/v1/sources").json() == {"prometheus": {"enabled": False}}


def test_empty_env_vars_are_ignored(monkeypatch):
    monkeypatch.setenv("PREDICTIVEOPS_API_KEY", "")
    assert Settings(_env_file=None).api_key is None
