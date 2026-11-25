"""Live, label-free host capacity forecasting from actual Prometheus telemetry.

This is a deterministic trend forecast, not a calibrated failure probability. It predicts
whether observed host resource metrics are on course to cross operational guardrails.
"""

from __future__ import annotations

from statistics import median

import pandas as pd

REQUIRED_HISTORY = 30
FORECAST_HORIZON_MINUTES = 30

# These are operational guardrails, not learned labels or failure probabilities.
RESOURCE_LIMITS: dict[str, tuple[float, float]] = {
    "cpu_pct": (85.0, 95.0),
    "memory_pct": (85.0, 95.0),
    "disk_io_util_pct": (85.0, 95.0),
}

METRIC_LABELS = {
    "cpu_pct": "CPU utilization",
    "memory_pct": "Memory utilization",
    "disk_io_util_pct": "Disk I/O utilization",
}


def _bounded(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 2)


def _theil_sen_slope(values: list[float]) -> float:
    """Median of all pairwise slopes; less sensitive to isolated metric spikes."""
    slopes = [
        (right - left) / (right_index - left_index)
        for left_index, left in enumerate(values)
        for right_index, right in enumerate(values[left_index + 1 :], start=left_index + 1)
    ]
    return median(slopes) if slopes else 0.0


def _crossing_minutes(current: float, slope: float, threshold: float) -> int | None:
    if current >= threshold:
        return 0
    if slope <= 0:
        return None
    return max(0, int(round((threshold - current) / slope)))


def forecast_host_metrics(frame: pd.DataFrame, *, series_id: str) -> dict:
    """Forecast 15/30-minute resource values from the latest 30 contiguous samples."""
    if len(frame) < REQUIRED_HISTORY:
        return {
            "status": "warming_up",
            "series_id": series_id,
            "samples_available": len(frame),
            "required_history": REQUIRED_HISTORY,
            "horizon_minutes": FORECAST_HORIZON_MINUTES,
            "metrics": [],
            "message": "Waiting for 30 contiguous one-minute host samples.",
        }

    ordered = frame.sort_values("timestamp").tail(REQUIRED_HISTORY).reset_index(drop=True)
    deltas = ordered["timestamp"].diff().dropna()
    if not deltas.empty and not (deltas == pd.Timedelta(minutes=1)).all():
        return {
            "status": "warming_up",
            "series_id": series_id,
            "samples_available": len(frame),
            "required_history": REQUIRED_HISTORY,
            "horizon_minutes": FORECAST_HORIZON_MINUTES,
            "metrics": [],
            "message": "Host telemetry contains a gap; rebuilding contiguous history.",
        }

    results: list[dict] = []
    severity = {"normal": 0, "warning": 1, "critical": 2}
    overall = "normal"

    for metric, (warning_limit, critical_limit) in RESOURCE_LIMITS.items():
        values = [float(value) for value in ordered[metric].tail(16)]
        current = values[-1]
        # Theil-Sen slope uses all pairs in the recent window and resists isolated spikes.
        slope = _theil_sen_slope(values)
        forecast_15 = _bounded(current + slope * 15)
        forecast_30 = _bounded(current + slope * FORECAST_HORIZON_MINUTES)

        if current >= critical_limit or forecast_30 >= critical_limit:
            status = "critical"
        elif current >= warning_limit or forecast_30 >= warning_limit:
            status = "warning"
        else:
            status = "normal"

        if severity[status] > severity[overall]:
            overall = status

        if slope > 0.02:
            trend = "rising"
        elif slope < -0.02:
            trend = "falling"
        else:
            trend = "stable"

        pressure = max(current, forecast_30) / critical_limit * 100.0
        results.append({
            "metric": metric,
            "label": METRIC_LABELS[metric],
            "current": _bounded(current),
            "slope_per_minute": round(slope, 4),
            "trend": trend,
            "forecast_15m": forecast_15,
            "forecast_30m": forecast_30,
            "warning_threshold": warning_limit,
            "critical_threshold": critical_limit,
            "minutes_to_warning": _crossing_minutes(current, slope, warning_limit),
            "minutes_to_critical": _crossing_minutes(current, slope, critical_limit),
            "pressure_index": round(min(100.0, max(0.0, pressure)), 1),
            "status": status,
        })

    return {
        "status": overall,
        "series_id": series_id,
        "samples_available": len(frame),
        "samples_used": REQUIRED_HISTORY,
        "horizon_minutes": FORECAST_HORIZON_MINUTES,
        "method": "theil_sen_linear_trend",
        "risk_semantics": "capacity_pressure_index_not_probability",
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "metrics": results,
        "message": (
            "At least one resource is projected to cross a critical guardrail."
            if overall == "critical"
            else "At least one resource is projected to cross a warning guardrail."
            if overall == "warning"
            else "No configured resource guardrail is projected to be crossed in 30 minutes."
        ),
    }
