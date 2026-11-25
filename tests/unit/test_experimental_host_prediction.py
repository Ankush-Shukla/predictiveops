from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from types import SimpleNamespace

import pandas as pd

from app.api.routers.system import experimental_host_prediction


class Buffer:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def frame(self) -> pd.DataFrame:
        return self._frame.copy()


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    def labels(self, **_labels):
        return self

    def inc(self, amount: int = 1) -> None:
        self.calls += amount


def make_state(sample_count: int = 30, *, with_model: bool = True):
    now = datetime.now(UTC)
    metrics = {
        "cpu_pct": 30.0,
        "memory_pct": 55.0,
        "disk_io_util_pct": 12.0,
        "network_rx_mbps": 1.2,
        "network_tx_mbps": 0.8,
    }
    frame = pd.DataFrame([
        {"timestamp": now - timedelta(minutes=sample_count - index - 1), **metrics}
        for index in range(sample_count)
    ])
    scored_at = now
    predictor = None
    if with_model:
        latest = SimpleNamespace(
            probability=0.17,
            threshold=0.22,
            predicted_failure=False,
            horizon_minutes=20,
            model_version="experimental-host-v1",
            as_of=scored_at,
        )

        def predict_recent(_frame, n):
            assert n == sample_count - 29
            return [latest]

        predictor = SimpleNamespace(
            required_history=30,
            metric_columns=tuple(metrics),
            version="experimental-host-v1",
            bundle=SimpleNamespace(metadata={
                "evaluation": {"gates": {"passed": True}},
            }),
            predict_recent=predict_recent,
        )

    counter = Counter()
    return SimpleNamespace(
        experimental_host_predictor=predictor,
        settings=SimpleNamespace(prometheus_host_series_id="home-server"),
        lock=Lock(),
        buffers={"host:home-server": Buffer(frame)},
        collectors=SimpleNamespace(predictions=counter),
    ), counter


def test_experimental_host_prediction_scores_live_buffer_without_policy_side_effects():
    state, counter = make_state()
    result = experimental_host_prediction(state)

    assert result["status"] == "scored"
    assert result["scope"] == "host"
    assert result["series_id"] == "host:home-server"
    assert result["probability"] == 0.17
    assert result["threshold"] == 0.22
    assert result["data_source"] == "synthetic"
    assert result["quality_gates_passed"] is True
    assert result["metrics"]["cpu_pct"] == 30.0
    assert counter.calls == 1


def test_experimental_host_prediction_waits_for_required_history():
    state, counter = make_state(sample_count=29)
    result = experimental_host_prediction(state)

    assert result["status"] == "warming_up"
    assert result["buffered_samples"] == 29
    assert result["required_history_minutes"] == 30
    assert counter.calls == 0


def test_experimental_host_prediction_reports_missing_model():
    state, counter = make_state(with_model=False)
    result = experimental_host_prediction(state)

    assert result["status"] == "model_unavailable"
    assert result["scope"] == "host"
    assert counter.calls == 0
