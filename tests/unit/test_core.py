import json
import logging

import pandas as pd
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.domain import METRIC_COLUMNS, HealthThresholds, compute_unhealthy
from app.core.exceptions import DataValidationError
from app.core.logging import configure_logging
from app.models.schemas import MetricSample, validate_metrics_frame


def test_settings_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("PREDICTIVEOPS_DRY_RUN", raising=False)
    s = Settings(_env_file=None)
    assert s.dry_run is True
    assert 15 <= s.horizon_minutes <= 30


def test_settings_env_override_and_horizon_bounds(monkeypatch):
    monkeypatch.setenv("PREDICTIVEOPS_HORIZON_MINUTES", "25")
    assert Settings(_env_file=None).horizon_minutes == 25
    monkeypatch.setenv("PREDICTIVEOPS_HORIZON_MINUTES", "5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_json_logging_includes_extras(capsys):
    configure_logging("INFO", json_logs=True)
    logging.getLogger("t").info("hello", extra={"foo": 1})
    rec = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert rec["message"] == "hello" and rec["foo"] == 1 and rec["level"] == "INFO"


def test_configure_logging_idempotent():
    configure_logging("INFO")
    configure_logging("INFO")
    n = sum(getattr(h, "_predictiveops", False) for h in logging.getLogger().handlers)
    assert n == 1


def _row(**kw):
    base = dict(timestamp="2025-01-01T00:00:00Z", series_id="a", cpu_pct=10, memory_pct=50,
                disk_io_util_pct=5, network_rx_mbps=1, network_tx_mbps=1, request_rate_rps=10,
                latency_p95_ms=100, error_rate=0.01)
    base.update(kw)
    return base


def test_metric_sample_validation():
    MetricSample(**_row())
    with pytest.raises(ValidationError):
        MetricSample(**_row(cpu_pct=150))
    with pytest.raises(ValidationError):
        MetricSample(**_row(error_rate=-0.1))


def test_validate_frame():
    df = pd.DataFrame([_row(), _row()])
    validate_metrics_frame(df)
    with pytest.raises(DataValidationError, match="missing"):
        validate_metrics_frame(df.drop(columns=["cpu_pct"]))
    bad = df.copy()
    bad.loc[0, "memory_pct"] = float("nan")
    with pytest.raises(DataValidationError, match="NaN"):
        validate_metrics_frame(bad)
    bad = df.copy()
    bad.loc[0, "error_rate"] = 2
    with pytest.raises(DataValidationError, match="outside"):
        validate_metrics_frame(bad)


def test_compute_unhealthy_requires_sustained_breach():
    n = 10
    df = pd.DataFrame({c: [10.0] * n for c in METRIC_COLUMNS})
    df["error_rate"] = 0.0
    df.loc[2:3, "latency_p95_ms"] = 5000  # 2 minutes -> transient, not unhealthy
    assert not compute_unhealthy(df, HealthThresholds(sustain_minutes=3)).any()
    df.loc[6:8, "latency_p95_ms"] = 5000  # 3 minutes -> unhealthy at the 3rd minute onward
    u = compute_unhealthy(df, HealthThresholds(sustain_minutes=3))
    assert list(u.nonzero()[0]) == [8]


def test_key_value_logging_format(capsys):
    configure_logging("INFO", json_logs=False)
    logging.getLogger("kv").warning("careful", extra={"x": 5})
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert "WARNING" in line and "careful" in line and "x=5" in line
    configure_logging("INFO", json_logs=True)
