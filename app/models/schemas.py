"""Pydantic schemas and DataFrame validation for metric data."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from pydantic import BaseModel, Field

from app.core.domain import METRIC_BOUNDS, METRIC_COLUMNS
from app.core.exceptions import DataValidationError


class MetricPoint(BaseModel):
    """One observation of a service's metrics at a point in time (1-minute resolution)."""

    timestamp: datetime
    cpu_pct: float = Field(ge=0, le=100)
    memory_pct: float = Field(ge=0, le=100)
    disk_io_util_pct: float = Field(ge=0, le=100)
    network_rx_mbps: float = Field(ge=0)
    network_tx_mbps: float = Field(ge=0)
    request_rate_rps: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)


class MetricSample(MetricPoint):
    series_id: str = Field(min_length=1)


def points_to_frame(points: list[MetricPoint]) -> pd.DataFrame:
    """Convert API points to a UTC-timestamped, time-sorted DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in points])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def validate_metrics_frame(df: pd.DataFrame, *, require_series_id: bool = True) -> None:
    """Raise DataValidationError if the frame is missing columns, has NaNs or out-of-range values."""
    required = list(METRIC_COLUMNS) + ["timestamp"] + (["series_id"] if require_series_id else [])
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataValidationError(f"missing columns: {missing}")
    if df.empty:
        raise DataValidationError("metrics frame is empty")
    for col in METRIC_COLUMNS:
        s = df[col]
        if s.isna().any():
            raise DataValidationError(f"column {col!r} contains NaN")
        lo, hi = METRIC_BOUNDS[col]
        if (s < lo).any() or (s > hi).any():
            raise DataValidationError(f"column {col!r} outside [{lo}, {hi}]")


class PredictionResult(BaseModel):
    """Output of a single failure-probability prediction."""

    probability: float = Field(ge=0, le=1, description="Calibrated P(failure within horizon)")
    threshold: float = Field(ge=0, le=1, description="Decision threshold chosen on validation data")
    predicted_failure: bool
    horizon_minutes: int
    model_version: str
    data_source: str = Field(description="Provenance of the training data, e.g. 'synthetic'")
    as_of: datetime
