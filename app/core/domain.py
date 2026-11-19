"""Core domain definitions shared by data generation, training and inference.

Keeping the metric list and the *definition of "unhealthy"* in one place guarantees
that synthetic labels, training labels and runtime health checks agree.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Canonical metric order. Training and inference MUST use this exact ordering.
METRIC_COLUMNS: tuple[str, ...] = (
    "cpu_pct",
    "memory_pct",
    "disk_io_util_pct",
    "network_rx_mbps",
    "network_tx_mbps",
    "request_rate_rps",
    "latency_p95_ms",
    "error_rate",
)

# Valid (min, max) per metric, used for validation.
METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    "cpu_pct": (0.0, 100.0),
    "memory_pct": (0.0, 100.0),
    "disk_io_util_pct": (0.0, 100.0),
    "network_rx_mbps": (0.0, 1e6),
    "network_tx_mbps": (0.0, 1e6),
    "request_rate_rps": (0.0, 1e7),
    "latency_p95_ms": (0.0, 1e7),
    "error_rate": (0.0, 1.0),
}


@dataclass(frozen=True)
class HealthThresholds:
    """A service-minute is 'raw unhealthy' if ANY threshold is breached.

    It is 'unhealthy' only once breached for `sustain_minutes` consecutive samples,
    which avoids labelling transient spikes as failures.
    """

    error_rate: float = 0.05
    latency_p95_ms: float = 1000.0
    memory_pct: float = 95.0
    cpu_pct: float = 98.0
    disk_io_util_pct: float = 98.0
    sustain_minutes: int = 3


def compute_unhealthy(
    metrics: pd.DataFrame, thresholds: HealthThresholds = HealthThresholds()
) -> np.ndarray:
    """Boolean array: True where the service is in a sustained unhealthy state.

    `metrics` must be a single, time-ordered, 1-minute-resolution series.
    """
    raw = (
        (metrics["error_rate"].to_numpy() >= thresholds.error_rate)
        | (metrics["latency_p95_ms"].to_numpy() >= thresholds.latency_p95_ms)
        | (metrics["memory_pct"].to_numpy() >= thresholds.memory_pct)
        | (metrics["cpu_pct"].to_numpy() >= thresholds.cpu_pct)
        | (metrics["disk_io_util_pct"].to_numpy() >= thresholds.disk_io_util_pct)
    )
    k = thresholds.sustain_minutes
    if k <= 1:
        return raw
    run = np.convolve(raw.astype(int), np.ones(k, dtype=int), mode="full")[: len(raw)]
    return run >= k
