import pandas as pd

from app.core.domain import HOST_METRIC_COLUMNS
from app.services.live_forecast import forecast_host_metrics


def frame_with_trends(cpu_values):
    rows = []
    start = pd.Timestamp("2026-11-25T00:00:00Z")
    for index, cpu in enumerate(cpu_values):
        rows.append({
            "timestamp": start + pd.Timedelta(minutes=index),
            "cpu_pct": cpu,
            "memory_pct": 50.0,
            "disk_io_util_pct": 20.0,
            "network_rx_mbps": 1.0,
            "network_tx_mbps": 1.0,
        })
    return pd.DataFrame(rows, columns=["timestamp", *HOST_METRIC_COLUMNS])


def test_forecast_warms_up_until_thirty_samples():
    result = forecast_host_metrics(frame_with_trends([40.0] * 29), series_id="host:test")
    assert result["status"] == "warming_up"
    assert result["required_history"] == 30


def test_forecast_detects_a_live_upward_cpu_trend():
    result = forecast_host_metrics(
        frame_with_trends([40.0 + index for index in range(30)]),
        series_id="host:test",
    )
    cpu = next(item for item in result["metrics"] if item["metric"] == "cpu_pct")
    assert result["status"] == "critical"
    assert cpu["trend"] == "rising"
    assert cpu["forecast_15m"] == 84.0
    assert cpu["forecast_30m"] == 99.0
    assert cpu["minutes_to_critical"] == 26
    assert result["risk_semantics"] == "capacity_pressure_index_not_probability"


def test_flat_healthy_telemetry_stays_normal():
    result = forecast_host_metrics(
        frame_with_trends([40.0] * 30),
        series_id="host:test",
    )
    assert result["status"] == "normal"
    assert all(item["trend"] == "stable" for item in result["metrics"])


def test_forecast_rejects_a_gap_in_the_sample_clock():
    frame = frame_with_trends([40.0] * 30)
    frame.loc[10:, "timestamp"] += pd.Timedelta(minutes=1)
    result = forecast_host_metrics(frame, series_id="host:test")
    assert result["status"] == "warming_up"
    assert "gap" in result["message"]
