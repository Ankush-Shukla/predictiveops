"""Deterministic short-horizon projections for dashboard resource charts.

These are trend projections from real Prometheus samples, not ML probabilities or
measurements. Missing or discontinuous history is reported as warming_up.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

REQUIRED_SAMPLES = 30
DEFAULT_HORIZON_MINUTES = 20
STEP_SECONDS = 60


def _slope(values: list[float]) -> float:
    slopes = [
        (right - left) / (right_index - left_index)
        for left_index, left in enumerate(values)
        for right_index, right in enumerate(values[left_index + 1 :], start=left_index + 1)
    ]
    return median(slopes) if slopes else 0.0


def project_resource_points(
    points: list[dict[str, Any]],
    metrics: list[str],
    *,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    bounded_metrics: set[str] | None = None,
) -> dict[str, Any]:
    """Project each metric from 30 contiguous one-minute samples into future timestamps.

    Each forecast series includes an anchor point at the final observed timestamp so
    the UI can draw a continuous dashed line from the measured line into the future.
    """
    bounded_metrics = bounded_metrics or set()
    ordered = sorted(
        (point for point in points if isinstance(point.get("t"), (int, float))),
        key=lambda point: float(point["t"]),
    )
    result: dict[str, Any] = {
        "status": "warming_up",
        "required_samples": REQUIRED_SAMPLES,
        "samples_available": min(len(ordered), REQUIRED_SAMPLES),
        "horizon_minutes": horizon_minutes,
        "forecast_points": [],
        "metrics": {},
        "anchor_timestamp": float(ordered[-1]["t"]) if ordered else None,
    }
    if len(ordered) < REQUIRED_SAMPLES:
        result["message"] = f"Waiting for {REQUIRED_SAMPLES} one-minute samples."
        return result

    forecasts: dict[str, list[float]] = {}
    metric_status: dict[str, str] = {}
    metric_sample_counts: dict[str, int] = {}
    for metric in metrics:
        valid = [
            (float(point["t"]), float(point[metric]))
            for point in ordered if isinstance(point.get(metric), (int, float))
            and math.isfinite(float(point[metric]))
        ][-REQUIRED_SAMPLES:]
        metric_sample_counts[metric] = len(valid)
        if len(valid) < REQUIRED_SAMPLES:
            metric_status[metric] = "warming_up"
            continue
        timestamps = [item[0] for item in valid]
        if any(abs((right - left) - STEP_SECONDS) > 2 for left, right in zip(timestamps, timestamps[1:], strict=False)):
            metric_status[metric] = "warming_up"
            continue

        values = [item[1] for item in valid]
        slope = _slope(values[-16:])
        projected = [values[-1]]
        for minute in range(1, horizon_minutes + 1):
            value = values[-1] + slope * minute
            value = max(0.0, value)
            if metric in bounded_metrics:
                value = min(100.0, value)
            projected.append(round(value, 4))
        forecasts[metric] = projected
        metric_status[metric] = "ready"

    result["samples_available"] = (
        min(metric_sample_counts.values()) if metric_sample_counts
        else min(len(ordered), REQUIRED_SAMPLES)
    )
    if not forecasts:
        result["message"] = "Waiting for 30 contiguous one-minute samples for each resource."
        result["metric_status"] = metric_status
        return result

    anchor = float(ordered[-1]["t"])
    result["forecast_points"] = []
    for index in range(horizon_minutes + 1):
        point = {
            "t": anchor + index * STEP_SECONDS,
            **{f"{metric}_forecast": values[index] for metric, values in forecasts.items()},
        }
        if index == 0:
            # Include the last measured values at the forecast boundary so solid and
            # dotted chart series meet at the same timestamp without implying a gap.
            point.update({metric: values[0] for metric, values in forecasts.items()})
        result["forecast_points"].append(point)
    result["metrics"] = {
        metric: {
            "current": values[0],
            "forecast": values[-1],
            "change": round(values[-1] - values[0], 4),
            "slope_per_minute": round(_slope([
                float(point[metric]) for point in ordered
                if isinstance(point.get(metric), (int, float))
            ][-16:]), 5),
        }
        for metric, values in forecasts.items()
    }
    result["metric_status"] = metric_status
    result["status"] = "ready" if len(forecasts) == len(metrics) else "partial"
    result["message"] = (
        "Dotted lines are 20-minute trend projections from real telemetry, not measured values."
    )
    return result
