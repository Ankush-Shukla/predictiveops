"""Static-threshold baseline (what the synopsis calls the reactive/threshold approach).

score = max over metrics of value / warning_threshold; the rule alerts when score >= 1.
It uses only the CURRENT sample, which is exactly what simple threshold alerting does.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Warning-level thresholds (below the failure thresholds in app.core.domain).
DEFAULT_WARN_THRESHOLDS: dict[str, float] = {
    "cpu_pct": 80.0,
    "memory_pct": 85.0,
    "disk_io_util_pct": 85.0,
    "latency_p95_ms": 500.0,
    "error_rate": 0.02,
}


def threshold_score(features: pd.DataFrame, thresholds: dict[str, float] | None = None) -> np.ndarray:
    th = thresholds or DEFAULT_WARN_THRESHOLDS
    ratios = np.column_stack([features[f"{m}__raw"].to_numpy() / v for m, v in th.items()])
    return ratios.max(axis=1)
