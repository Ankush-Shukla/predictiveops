"""Read-only endpoints that expose real Prometheus telemetry to the dashboard."""

from __future__ import annotations

import asyncio
import time

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
    names = list(HOST_HISTORY_QUERIES)
    try:
        async with prometheus_client(state.settings.prometheus_url) as client:
            responses = await asyncio.gather(*(
                client.get("/api/v1/query_range", params={
                    "query": query, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": str(step)})
                for query in HOST_HISTORY_QUERIES.values()
            ))
        series = {}
        for name, response in zip(names, responses, strict=True):
            response.raise_for_status()
            series[name] = matrix_to_map(response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return {"available": False, "minutes": minutes, "step_seconds": step,
                "points": [], "latest": {}, "error": str(exc)}
    points = merge_points(series)
    return {
        "available": bool(points),
        "minutes": minutes,
        "step_seconds": step,
        "series_id": state.settings.prometheus_host_series_id,
        "points": points,
        "latest": latest_values(points, names),
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
