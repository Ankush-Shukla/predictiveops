"""Pure helpers that shape real Prometheus telemetry for the dashboard.

Nothing here generates or simulates data: every value comes from a Prometheus response. The
functions only parse, align and summarise, so they can be unit-tested without a server.
"""

from __future__ import annotations

import math
from typing import Any

from app.services.prometheus_client import HOST_QUERIES

# The five model-input metrics reuse the exact production queries, so the charts show what the
# host model will actually see. Two extra display-only series add context.
HOST_HISTORY_QUERIES: dict[str, str] = {
    **HOST_QUERIES,
    "load1": "sum(node_load1)",
    "root_disk_used_pct": (
        '100 * (1 - max(node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs"}) '
        '/ max(node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs"}))'
    ),
}

MAX_POINTS = 240
MIN_STEP_SECONDS = 15


def pick_step(minutes: int) -> int:
    """Smallest step (>= scrape interval) that keeps the response under MAX_POINTS."""
    return max(MIN_STEP_SECONDS, math.ceil(minutes * 60 / MAX_POINTS / MIN_STEP_SECONDS) * MIN_STEP_SECONDS)


def matrix_to_map(payload: dict[str, Any]) -> dict[float, float]:
    """Turn a Prometheus query_range response for a single-series query into {timestamp: value}.

    Raises ValueError on an unsuccessful response or when the query matched several series.
    Non-finite samples (NaN/Inf) are dropped rather than turned into zero.
    """
    if payload.get("status") != "success":
        raise ValueError(str(payload.get("error", "Prometheus returned an unsuccessful query")))
    result = payload.get("data", {}).get("result", [])
    if len(result) > 1:
        raise ValueError("query returned several series")
    if not result:
        return {}
    out: dict[float, float] = {}
    for ts, raw in result[0].get("values", []):
        value = float(raw)
        if math.isfinite(value):
            out[float(ts)] = value
    return out


def merge_points(series: dict[str, dict[float, float]]) -> list[dict[str, float | None]]:
    """Align several {timestamp: value} maps on a shared time axis. Missing values stay None."""
    timestamps = sorted({ts for values in series.values() for ts in values})
    return [{"t": ts, **{name: values.get(ts) for name, values in series.items()}} for ts in timestamps]


def latest_values(points: list[dict[str, float | None]], names: list[str]) -> dict[str, float | None]:
    """Most recent non-missing value per metric."""
    latest: dict[str, float | None] = dict.fromkeys(names)
    for point in reversed(points):
        for name in names:
            if latest[name] is None and point.get(name) is not None:
                latest[name] = point[name]
        if all(v is not None for v in latest.values()):
            break
    return latest


def summarize_coverage(payload: dict[str, Any], step_seconds: int) -> dict[str, Any]:
    """How much real host telemetry Prometheus holds, from an `up` query_range response.

    A bucket counts as covered only when the exporter reported up == 1 for it.
    """
    samples = matrix_to_map(payload)
    covered = sorted(ts for ts, value in samples.items() if value >= 1)
    return {
        "first_sample": covered[0] if covered else None,
        "last_sample": covered[-1] if covered else None,
        "covered_hours": round(len(covered) * step_seconds / 3600, 1),
    }


def parse_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce Prometheus /api/v1/alerts to the fields the dashboard shows, firing first."""
    if payload.get("status") != "success":
        raise ValueError(str(payload.get("error", "Prometheus returned an unsuccessful query")))
    order = {"firing": 0, "pending": 1}
    rows = []
    for alert in payload.get("data", {}).get("alerts", []):
        labels = alert.get("labels", {})
        rows.append({
            "name": labels.get("alertname", "unknown"),
            "state": alert.get("state", "unknown"),
            "severity": labels.get("severity", "none"),
            "scope": labels.get("scope"),
            "source": labels.get("source"),
            "summary": alert.get("annotations", {}).get("summary", ""),
            "active_at": alert.get("activeAt"),
            "value": alert.get("value"),
        })
    return sorted(rows, key=lambda r: (order.get(r["state"], 2), r["name"]))
