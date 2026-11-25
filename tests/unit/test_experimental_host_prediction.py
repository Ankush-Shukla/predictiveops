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
        {
            "timestamp": now - timedelta(minutes=sample_count - index - 1),
            **metrics,
        }
        for index in range(sample_count)
    ])

    predictor = None
    if with_model:
        latest = SimpleNamespace(
            probability=0.17,
            threshold=0.22,
            predicted_failure=False,
            horizon_minutes=20,
            model_version="experimental-host-v1",
            as_of=now,
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

    production_predictor = object()
    host_scope_predictor = object()
    application_scope_predictor = object()
    latest_predictions = {"host:home-server": object()}
    policy_state = {
        "alerts": ["existing-alert"],
        "actions": ["existing-action"],
    }
    policy = SimpleNamespace(state=policy_state)
    prediction_counter = Counter()
    alert_counter = Counter()
    action_counter = Counter()

    state = SimpleNamespace(
        experimental_host_predictor=predictor,
        predictor=production_predictor,
        scope_predictors={
            "host": host_scope_predictor,
            "application": application_scope_predictor,
        },
        latest=latest_predictions,
        policy=policy,
        settings=SimpleNamespace(
            prometheus_host_series_id="home-server",
        ),
        lock=Lock(),
        buffers={"host:home-server": Buffer(frame)},
        collectors=SimpleNamespace(
            predictions=prediction_counter,
            alerts=alert_counter,
            actions=action_counter,
        ),
    )

    production_snapshot = {
        "predictor": state.predictor,
        "scope_predictors": dict(state.scope_predictors),
        "latest": dict(state.latest),
        "policy": dict(policy.state),
    }
    return state, prediction_counter, alert_counter, action_counter, production_snapshot


def assert_production_state_unchanged(state, snapshot):
    assert state.predictor is snapshot["predictor"]
    assert state.scope_predictors == snapshot["scope_predictors"]
    assert state.latest == snapshot["latest"]
    assert state.policy.state == snapshot["policy"]


def test_experimental_host_prediction_scores_without_production_side_effects():
    state, predictions, alerts, actions, snapshot = make_state()

    result = experimental_host_prediction(state)

    assert result["status"] == "scored"
    assert result["scope"] == "host"
    assert result["series_id"] == "host:home-server"
    assert result["probability"] == 0.17
    assert result["threshold"] == 0.22
    assert result["data_source"] == "synthetic"
    assert result["quality_gates_passed"] is True
    assert result["metrics"]["cpu_pct"] == 30.0

    assert predictions.calls == 1
    assert alerts.calls == 0
    assert actions.calls == 0
    assert_production_state_unchanged(state, snapshot)


def test_experimental_host_prediction_waits_for_required_history():
    state, predictions, alerts, actions, snapshot = make_state(sample_count=29)

    result = experimental_host_prediction(state)

    assert result["status"] == "warming_up"
    assert result["buffered_samples"] == 29
    assert result["required_history_minutes"] == 30
    assert predictions.calls == 0
    assert alerts.calls == 0
    assert actions.calls == 0
    assert_production_state_unchanged(state, snapshot)


def test_experimental_host_prediction_reports_missing_model():
    state, predictions, alerts, actions, snapshot = make_state(with_model=False)

    result = experimental_host_prediction(state)

    assert result["status"] == "model_unavailable"
    assert result["scope"] == "host"
    assert predictions.calls == 0
    assert alerts.calls == 0
    assert actions.calls == 0
    assert_production_state_unchanged(state, snapshot)


def test_experimental_host_prediction_accepts_unprefixed_host_buffer():
    state, predictions, alerts, actions, snapshot = make_state()
    state.buffers = {"home-server": state.buffers["host:home-server"]}

    result = experimental_host_prediction(state)

    assert result["status"] == "scored"
    assert result["series_id"] == "home-server"
    assert predictions.calls == 1
    assert alerts.calls == 0
    assert actions.calls == 0
    assert_production_state_unchanged(state, snapshot)
