"""Scripted single-series scenarios for demos (SYNTHETIC; used by the dashboard)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.domain import METRIC_COLUMNS, HealthThresholds, compute_unhealthy
from app.core.exceptions import DataGenerationError
from data.synthetic.generator import (
    DATA_SOURCE,
    FAULT_TYPES,
    STEP_SECONDS,
    _apply_fault,
    _baseline,
    add_labels,
    add_spikes,
)

SCENARIO_START = "2025-01-06T08:00:00+00:00"


def generate_scenario(
    fault_type: str,
    *,
    minutes: int = 360,
    fault_start: int = 120,
    ramp_minutes: int = 60,
    hold_minutes: int = 10,
    seed: int = 42,
    spikes: bool = True,
    horizon_minutes: int = 20,
    min_fault_start: int = 60,
) -> pd.DataFrame:
    """One series: healthy baseline, optional distractor spikes, optional single fault episode.

    ``fault_type`` is one of FAULT_TYPES or "none" (healthy run).
    """
    if fault_type != "none" and fault_type not in FAULT_TYPES:
        raise DataGenerationError(f"unknown fault_type {fault_type!r}")
    if not 120 <= minutes <= 1440:
        raise DataGenerationError("minutes must be within 120-1440")
    if fault_type != "none":
        if fault_start < min_fault_start:
            raise DataGenerationError(f"fault_start must be >= {min_fault_start}")
        if not 10 <= ramp_minutes <= 180:
            raise DataGenerationError("ramp_minutes must be within 10-180")
        if fault_start + ramp_minutes + hold_minutes >= minutes - horizon_minutes:
            raise DataGenerationError("fault does not fit: increase minutes or reduce fault_start/ramp")
    rng = np.random.default_rng(seed)
    m = _baseline(rng, minutes)
    if fault_type != "none":
        p = np.concatenate([np.linspace(1 / ramp_minutes, 1.0, ramp_minutes),
                            np.full(hold_minutes, 1.05)])
        _apply_fault(m, fault_type, slice(fault_start, fault_start + ramp_minutes + hold_minutes), p)
    if spikes:
        add_spikes(m, rng, minutes, int(rng.poisson(minutes / 1440 * 8)))
    for col in ("cpu_pct", "memory_pct", "disk_io_util_pct"):
        m[col] = np.clip(m[col], 0, 100)
    m["error_rate"] = np.clip(m["error_rate"], 0, 1)
    for col in ("network_rx_mbps", "network_tx_mbps", "request_rate_rps", "latency_p95_ms"):
        m[col] = np.clip(m[col], 0, None)
    df = pd.DataFrame({"timestamp": pd.date_range(SCENARIO_START, periods=minutes,
                                                  freq=f"{STEP_SECONDS}s"),
                       "series_id": "demo"})
    for c in METRIC_COLUMNS:
        df[c] = m[c]
    df["is_unhealthy"] = compute_unhealthy(df, HealthThresholds())
    df["fault_type"] = fault_type
    df["data_source"] = DATA_SOURCE
    return add_labels(df, horizon_minutes)
