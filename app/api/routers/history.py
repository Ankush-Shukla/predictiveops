"""Read-only endpoints that expose real Prometheus telemetry to the dashboard."""

from __future__ import annotations

import asyncio
import time

import math

from app.services.resource_forecast import project_resource_points

import httpx
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_state
from app.api.state import ApiState
from app.services.host_history import (
    HOST_HISTORY_QUERIES,
    latest_values,
    matrix_to_map,
    merge_points,
    parse_alerts,
    pick_step,
    summarize_coverage,
)

router = APIRouter(tags=["monitoring"])

COVERAGE_DAYS = 30
COVERAGE_STEP_SECONDS = 3600
TIMEOUT_SECONDS = 4.0


def prometheus_client(base_url: str) -> httpx.AsyncClient:
    """Factory kept separate so tests can inject a mock transport."""
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=TIMEOUT_SECONDS)


@router.get("/v1/host/history")
async def host_history(
    minutes: int = Query(60, ge=5, le=1440, description="Window length ending now"),
    state: ApiState = Depends(get_state),
) -> dict:
    """Real host metrics over time, using the same PromQL the host model consumes."""
    end = time.time()
    step = pick_step(minutes)
    start = end - minutes * 60
    forecast_start = end - 30 * 60
    names = list(HOST_HISTORY_QUERIES)
    try:
        async with prometheus_client(state.settings.prometheus_url) as client:
            history_responses, forecast_responses = await asyncio.gather(
                asyncio.gather(*(
                    client.get("/api/v1/query_range", params={
                        "query": query, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": str(step)})
                    for query in HOST_HISTORY_QUERIES.values()
                )),
                asyncio.gather(*(
                    client.get("/api/v1/query_range", params={
                        "query": query, "start": f"{forecast_start:.3f}",
                        "end": f"{end:.3f}", "step": "60"})
                    for query in HOST_HISTORY_QUERIES.values()
                )),
            )
        series = {}
        forecast_series = {}
        for name, response in zip(names, history_responses, strict=True):
            response.raise_for_status()
            series[name] = matrix_to_map(response.json())
        for name, response in zip(names, forecast_responses, strict=True):
            response.raise_for_status()
            forecast_series[name] = matrix_to_map(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "minutes": minutes, "step_seconds": step,
                "points": [], "latest": {}, "forecast_points": [], "forecast_status": "unavailable",
                "error": str(exc)}
    points = merge_points(series)
    forecast_input = merge_points(forecast_series)
    forecast = project_resource_points(
        forecast_input,
        names,
        horizon_minutes=20,
        bounded_metrics={"cpu_pct", "memory_pct", "disk_io_util_pct", "root_disk_used_pct"},
    )
    return {
        "available": bool(points),
        "minutes": minutes,
        "step_seconds": step,
        "series_id": state.settings.prometheus_host_series_id,
        "points": points,
        "latest": latest_values(points, names),
        "forecast_points": forecast["forecast_points"],
        "forecast_status": forecast["status"],
        "forecast_samples_available": forecast["samples_available"],
        "forecast_required_samples": forecast["required_samples"],
        "forecast_horizon_minutes": forecast["horizon_minutes"],
        "forecast_anchor_timestamp": forecast["anchor_timestamp"],
        "forecast_message": forecast["message"],
        "forecast_metrics": forecast["metrics"],
    }


@router.get("/v1/containers/forecast")
async def container_resource_forecasts(state: ApiState = Depends(get_state)) -> dict:
    """Return per-container CPU and memory trend projections from cAdvisor history."""
    end = time.time()
    start = end - 30 * 60
    expressions = {
        "cpu_pct": (
            '100 * sum by (name, container_label_com_docker_compose_service) '
            '(rate(container_cpu_usage_seconds_total{image!="",name!=""}[5m])) '
            '/ scalar(count(node_cpu_seconds_total{mode="idle"}))'
        ),
        "memory_gib": (
            'sum by (name, container_label_com_docker_compose_service) '
            '(container_memory_working_set_bytes{image!="",name!=""}) / 1073741824'
        ),
    }
    try:
        async with prometheus_client(state.settings.prometheus_url) as client:
            responses = await asyncio.gather(*(
                client.get("/api/v1/query_range", params={
                    "query": expression, "start": f"{start:.3f}",
                    "end": f"{end:.3f}", "step": "60"})
                for expression in expressions.values()
            ))
        containers: dict[str, dict] = {}
        for (metric, _), response in zip(expressions.items(), responses, strict=True):
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError(str(payload.get("error", "Prometheus query failed")))
            for item in payload.get("data", {}).get("result", []):
                labels = item.get("metric", {})
                name = labels.get("name")
                if not name:
                    continue
                row = containers.setdefault(name, {
                    "name": name,
                    "service": labels.get("container_label_com_docker_compose_service") or name,
                    "points_by_time": {},
                })
                for timestamp, raw_value in item.get("values", []):
                    value = float(raw_value)
                    if not math.isfinite(value):
                        continue
                    point = row["points_by_time"].setdefault(float(timestamp), {"t": float(timestamp)})
                    point[metric] = value
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "containers": [], "error": str(exc)}

    rows = []
    for row in containers.values():
        points = [row_point for _, row_point in sorted(row["points_by_time"].items())]
        forecast = project_resource_points(
            points, ["cpu_pct", "memory_gib"], horizon_minutes=20,
            bounded_metrics={"cpu_pct"},
        )
        rows.append({
            "name": row["name"],
            "service": row["service"],
            "status": forecast["status"],
            "samples_available": forecast["samples_available"],
            "required_samples": forecast["required_samples"],
            "horizon_minutes": forecast["horizon_minutes"],
            "points": points,
            "forecast_points": forecast["forecast_points"],
            "metrics": forecast["metrics"],
            "message": forecast["message"],
        })
    return {
        "available": bool(rows),
        "count": len(rows),
        "horizon_minutes": 20,
        "containers": sorted(rows, key=lambda row: row["name"].lower()),
    }


@router.get("/v1/telemetry/coverage")
async def telemetry_coverage(state: ApiState = Depends(get_state)) -> dict:
    """How much real host telemetry exists, i.e. how close we are to having training data."""
    end = time.time()
    start = end - COVERAGE_DAYS * 86400
    try:
        async with prometheus_client(state.settings.prometheus_url) as client:
            response = await client.get("/api/v1/query_range", params={
                "query": 'max(up{job="node-exporter"})', "start": f"{start:.0f}",
                "end": f"{end:.0f}", "step": str(COVERAGE_STEP_SECONDS)})
        response.raise_for_status()
        summary = summarize_coverage(response.json(), COVERAGE_STEP_SECONDS)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "error": str(exc)}
    return {"available": True, "window_days": COVERAGE_DAYS, **summary}


@router.get("/v1/infra/alerts")
async def infra_alerts(state: ApiState = Depends(get_state)) -> dict:
    """Pending and firing alerts from Prometheus, including the static safety-net rules."""
    try:
        async with prometheus_client(state.settings.prometheus_url) as client:
            response = await client.get("/api/v1/alerts")
        response.raise_for_status()
        alerts = parse_alerts(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "alerts": [], "error": str(exc)}
    return {"available": True, "alerts": alerts, "firing": sum(a["state"] == "firing" for a in alerts)}
