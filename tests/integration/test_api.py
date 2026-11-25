import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import Settings
from app.models.predictor import Predictor
from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

METRICS = ["cpu_pct", "memory_pct", "disk_io_util_pct", "network_rx_mbps", "network_tx_mbps",
           "request_rate_rps", "latency_p95_ms", "error_rate"]


@pytest.fixture(scope="module")
def data():
    return generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))


@pytest.fixture(scope="module")
def predictor(data, tmp_path_factory):
    root = tmp_path_factory.mktemp("m")
    res = run_training(data, TrainConfig(model_type="logreg", model_root=root, use_mlflow=False,
                                         max_fpr_for_threshold=0.2, gates=GateConfig(min_f1=0.0)))
    return Predictor(res.bundle, res.version)


def settings(**kw):
    return Settings(_env_file=None, log_level="WARNING", **kw)


@pytest.fixture
def client(predictor):
    with TestClient(create_app(settings(), predictor, load_model=False)) as c:
        yield c


@pytest.fixture
def empty_client():
    with TestClient(create_app(settings(), None, load_model=False)) as c:
        yield c


def points(data, start, n, series="svc-00"):
    g = data[data.series_id == series].iloc[start:start + n]
    return [{"timestamp": r.timestamp.isoformat(), **{m: float(getattr(r, m)) for m in METRICS}}
            for r in g.itertuples()]


def test_container_inventory_degrades_cleanly_without_prometheus():
    app = create_app(settings(prometheus_url="http://127.0.0.1:1"), None, load_model=False)
    with TestClient(app) as c:
        response = c.get("/v1/containers")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["containers"] == []
    assert response.json()["error"]


def test_health_and_static(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/")
    assert r.status_code == 200 and "PredictiveOps" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert "x-request-id" in client.get("/health").headers


def test_not_ready_without_model(empty_client):
    assert empty_client.get("/health").status_code == 200
    assert empty_client.get("/ready").status_code == 503
    assert empty_client.get("/v1/model").status_code == 503
    r = empty_client.post("/v1/simulate", json={})
    assert r.status_code == 503 and r.json()["error"] == "model_not_ready"
    assert empty_client.post("/v1/predict", json={"samples": []}).status_code in (422, 503)


def test_ready_and_model_info(client, predictor):
    assert client.get("/ready").json()["model_version"] == predictor.version
    m = client.get("/v1/model").json()
    assert m["data_source"] == "synthetic" and m["n_features"] == 152
    assert m["required_history_minutes"] == 30 and "disclaimer" in m


def test_predict_matches_library(client, predictor, data):
    pts = points(data, 500, 60)
    r = client.post("/v1/predict", json={"samples": pts})
    assert r.status_code == 200
    body = r.json()
    expect = predictor.predict(pd.DataFrame(pts).assign(timestamp=lambda d: pd.to_datetime(d.timestamp)))
    assert body["probability"] == pytest.approx(expect.probability)
    assert body["model_version"] == predictor.version and body["data_source"] == "synthetic"
    assert 0 <= body["probability"] <= 1


def test_predict_validation_errors(client, data):
    short = client.post("/v1/predict", json={"samples": points(data, 0, 10)})
    assert short.status_code == 422 and short.json()["error"] == "invalid_data"
    bad = points(data, 0, 40)
    bad[3]["cpu_pct"] = 500
    assert client.post("/v1/predict", json={"samples": bad}).status_code == 422
    recent_gap = points(data, 0, 60)
    del recent_gap[50:53]  # inside the 30-minute feature window -> must be refused
    r = client.post("/v1/predict", json={"samples": recent_gap})
    assert r.status_code == 422 and "regular" in r.json()["detail"]
    old_gap = points(data, 0, 60)
    del old_gap[10:13]  # older than the feature window -> irrelevant to the features
    assert client.post("/v1/predict", json={"samples": old_gap}).status_code == 200
    assert client.post("/v1/predict", json={"samples": []}).status_code == 422


def test_ingest_scores_after_enough_history_and_exposes_metrics(client, data):
    first = client.post("/v1/metrics/ingest", json={"series_id": "svc-a", "samples": points(data, 0, 20)})
    assert first.json()["prediction"] is None and first.json()["buffered"] == 20
    second = client.post("/v1/metrics/ingest", json={"series_id": "svc-a", "samples": points(data, 20, 20)})
    j = second.json()
    assert j["accepted"] == 20 and j["prediction"] is not None and j["buffered"] == 40
    dup = client.post("/v1/metrics/ingest", json={"series_id": "svc-a", "samples": points(data, 30, 5)})
    assert dup.json()["rejected"] == 5 and dup.json()["accepted"] == 0
    gap = client.post("/v1/metrics/ingest", json={"series_id": "svc-a", "samples": points(data, 100, 3)})
    assert gap.json()["gap_resets"] == 1 and gap.json()["buffered"] == 3
    s = client.get("/v1/series").json()
    assert [x["series_id"] for x in s] == ["svc-a"]
    assert client.get("/v1/series/svc-a").json()["buffered"] == 3
    assert client.get("/v1/series/nope").status_code == 404
    text = client.get("/metrics").text
    assert 'predictiveops_failure_probability{series_id="svc-a"}' in text
    assert "predictiveops_ingested_samples_total 43.0" in text
    assert "predictiveops_model_info" in text


def test_ingest_rejects_bad_series_id(client, data):
    r = client.post("/v1/metrics/ingest", json={"series_id": "bad id/..", "samples": points(data, 0, 3)})
    assert r.status_code == 422


def test_api_key_protects_ingest_only(predictor, data):
    app = create_app(settings(api_key="s3cret"), predictor, load_model=False)
    with TestClient(app) as c:
        body = {"series_id": "x", "samples": points(data, 0, 5)}
        assert c.post("/v1/metrics/ingest", json=body).status_code == 401
        assert c.post("/v1/metrics/ingest", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.post("/v1/metrics/ingest", json=body, headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.post("/v1/predict", json={"samples": points(data, 0, 60)}).status_code == 200


def test_simulate_structure_and_errors(client):
    r = client.post("/v1/simulate", json={"fault_type": "memory_leak", "seed": 3})
    assert r.status_code == 200
    j = r.json()
    n = j["minutes"]
    assert j["data_source"] == "synthetic" and n == 360
    assert len(j["probability"]) == len(j["is_unhealthy"]) == len(j["label_failure_within_h"]) == n
    assert all(len(v) == n for v in j["metrics"].values()) and set(j["metrics"]) == set(METRICS)
    assert j["probability"][:29] == [None] * 29 and j["probability"][40] is not None
    assert j["summary"]["failure_start_minute"] is not None
    healthy = client.post("/v1/simulate", json={"fault_type": "none"}).json()
    assert healthy["summary"]["failure_start_minute"] is None
    bad = client.post("/v1/simulate", json={"fault_type": "memory_leak", "minutes": 200, "fault_start": 190})
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_scenario"
    assert client.post("/v1/simulate", json={"fault_type": "bogus"}).status_code == 422
