from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import get_state, production_model_block_reason
from app.api.state import ApiState
from app.core.exceptions import ModelNotReadyError
from app.models.predictor import Predictor
from app.services.live_forecast import forecast_host_metrics

router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


def _model_payload(p: Predictor) -> dict:
    m = p.bundle.metadata
    return {
        "scope": m.get("scope", "combined"),
        "version": p.version, "model_type": m.get("model_type"), "horizon_minutes": m.get("horizon_minutes"),
        "data_source": m.get("data_source"), "trained_at": m.get("trained_at"),
        "threshold": p.bundle.threshold, "required_history_minutes": p.required_history,
        "n_features": len(p.bundle.schema.names), "schema_hash": p.bundle.schema.schema_hash,
        "evaluation": m.get("evaluation"), "top_features": m.get("top_features_permutation"),
        "disclaimer": m.get("disclaimer"),
    }


@router.get("/ready", tags=["system"])
def ready(state: ApiState = Depends(get_state)):
    """Readiness requires every configured production prediction scope to be qualified."""
    if state.settings.env == "production":
        for scope in ("host", "application"):
            predictor = state.scope_predictors.get(scope)
            reason = production_model_block_reason(state, predictor, expected_scope=scope)
            if reason is not None:
                return JSONResponse(status_code=503, content={
                    "status": "not_ready", "scope": scope, "reason": reason,
                    "model_version": predictor.version if predictor else None,
                    "data_source": predictor.bundle.metadata.get("data_source") if predictor else None,
                })
        return {"status": "ready", "models": {
            scope: state.scope_predictors[scope].version for scope in ("host", "application")
        }}
    reason = production_model_block_reason(state)
    if reason is not None:
        return JSONResponse(status_code=503, content={
            "status": "not_ready", "reason": reason,
            "model_version": state.predictor.version if state.predictor else None,
            "data_source": state.predictor.bundle.metadata.get("data_source") if state.predictor else None,
        })
    return {"status": "ready", "model_version": state.predictor.version}


@router.get("/v1/model", tags=["model"])
def model_info(state: ApiState = Depends(get_state)) -> dict:
    if state.settings.env == "production":
        return {"models": {scope: _model_payload(p)
                           for scope, p in sorted(state.scope_predictors.items())}}
    return _model_payload(get_predictor_from_state(state))


def get_predictor_from_state(state: ApiState) -> Predictor:
    p = state.predictor
    reason = production_model_block_reason(state, p)
    if p is None or reason is not None:
        raise ModelNotReadyError(reason or "model is not ready")
    return p


@router.get("/v1/sources", tags=["system"])
def sources(request: Request) -> dict:
    """Status of every configured Prometheus ingestion scope."""
    pollers = getattr(request.app.state, "pollers", {})
    if request.app.state.api.settings.env == "production":
        return {"prometheus": {"enabled": True, "scopes": {
            scope: {
                "step_seconds": poller.step,
                "poll_seconds": poller.interval,
                "services": poller.status,
            }
            for scope, poller in sorted(pollers.items())
        }}}
    poller = request.app.state.poller
    if poller is None:
        return {"prometheus": {"enabled": False}}
    return {"prometheus": {"enabled": True, "step_seconds": poller.step, "services": poller.status}}


@router.get("/v1/experimental/host/prediction", tags=["experimental"])
def experimental_host_prediction(state: ApiState = Depends(get_state)) -> dict:
    """Score live host telemetry with an isolated synthetic-trained model.

    Observational only: this endpoint does not update production predictions,
    readiness, alerts, policy state, or remediation.
    """
    predictor = state.experimental_host_predictor
    if predictor is None:
        return {
            "status": "model_unavailable",
            "scope": "host",
            "disclaimer": "No experimental host model is installed.",
        }

    host_id = state.settings.prometheus_host_series_id
    candidates = (f"host:{host_id}", host_id)
    with state.lock:
        series_id = next((key for key in candidates if key in state.buffers), None)
        frame = state.buffers[series_id].frame() if series_id is not None else None

    buffered = len(frame) if frame is not None else 0
    if frame is None or buffered < predictor.required_history:
        return {
            "status": "warming_up",
            "scope": "host",
            "series_id": f"host:{host_id}",
            "model_version": predictor.version,
            "data_source": "synthetic",
            "buffered_samples": buffered,
            "required_history_minutes": predictor.required_history,
            "disclaimer": (
                "Synthetic-trained experimental model; live host telemetry is "
                "being buffered."
            ),
        }

    scored = predictor.predict_recent(
        frame, buffered - predictor.required_history + 1
    )
    if not scored:
        return {
            "status": "warming_up",
            "scope": "host",
            "series_id": series_id,
            "buffered_samples": buffered,
            "required_history_minutes": predictor.required_history,
        }

    latest = scored[-1]
    state.collectors.predictions.labels(source="experimental_host").inc()

    evaluation = predictor.bundle.metadata.get("evaluation", {})
    gates = evaluation.get("gates", {}) if isinstance(evaluation, dict) else {}
    disclaimer = (
        "EXPERIMENTAL: synthetic host-fault training data applied to live host "
        "metrics. Not production-qualified; no alerts or remediation are triggered."
    )

    return {
        "status": "scored",
        "scope": "host",
        "series_id": series_id,
        "model_version": latest.model_version,
        "data_source": "synthetic",
        "probability": latest.probability,
        "threshold": latest.threshold,
        "predicted_failure": latest.predicted_failure,
        "horizon_minutes": latest.horizon_minutes,
        "as_of": latest.as_of,
        "buffered_samples": buffered,
        "required_history_minutes": predictor.required_history,
        "history": [
            {"as_of": item.as_of, "probability": item.probability}
            for item in scored
        ],
        "metrics": {
            name: float(frame.iloc[-1][name])
            for name in predictor.metric_columns
        },
        "quality_gates_passed": gates.get("passed") is True,
        "evaluation": evaluation,
        "disclaimer": disclaimer,
    }


@router.get("/v1/live-forecast", tags=["predictions"])
def live_host_forecast(request: Request, state: ApiState = Depends(get_state)) -> dict:
    """Forecast host resource guardrail crossings from the actual live telemetry buffer."""
    host_id = state.settings.prometheus_host_series_id
    candidates = (f"host:{host_id}", host_id)
    with state.lock:
        series_id = next((key for key in candidates if key in state.buffers), None)
        if series_id is None:
            frame = None
        else:
            frame = state.buffers[series_id].frame()

    pollers = getattr(request.app.state, "pollers", {})
    poller = pollers.get("host") if isinstance(pollers, dict) else None
    poller = poller or getattr(request.app.state, "poller", None)
    target_status = poller.status.get(host_id, {}) if poller is not None else {}
    last_ok = target_status.get("last_ok")
    source_error = target_status.get("error")
    source_age = None
    if last_ok:
        try:
            parsed = datetime.fromisoformat(str(last_ok).replace("Z", "+00:00"))
            source_age = max(0.0, (datetime.now(UTC) - parsed).total_seconds())
        except ValueError:
            source_age = None

    if frame is None:
        result = {
            "status": "warming_up",
            "series_id": f"host:{host_id}",
            "samples_available": 0,
            "required_history": 30,
            "horizon_minutes": 30,
            "metrics": [],
            "message": "Waiting for the first complete host telemetry poll.",
        }
    else:
        result = forecast_host_metrics(frame, series_id=series_id)

    result.update({
        "source": "prometheus_node_exporter",
        "source_status": (
            "unavailable" if poller is None
            else "stale" if source_age is None or source_age > 180
            else "degraded" if source_error
            else "healthy"
        ),
        "source_last_ok": last_ok,
        "source_age_seconds": round(source_age, 1) if source_age is not None else None,
        "source_error": source_error,
    })
    return result


@router.get("/v1/containers", tags=["monitoring"])
async def containers(state: ApiState = Depends(get_state)) -> dict:
    """Return one live resource snapshot per Docker container from cAdvisor."""
    base_url = state.settings.prometheus_url.rstrip("/")
    expressions = {
        "cpu_cores": (
            'sum by (name, container_label_com_docker_compose_service) '
            '(rate(container_cpu_usage_seconds_total{image!="",name!=""}[5m]))'
        ),
        "memory_bytes": (
            'sum by (name, container_label_com_docker_compose_service) '
            '(container_memory_working_set_bytes{image!="",name!=""})'
        ),
        "network_rx_bytes_s": (
            'sum by (name, container_label_com_docker_compose_service) '
            '(rate(container_network_receive_bytes_total{image!="",name!=""}[5m]))'
        ),
        "network_tx_bytes_s": (
            'sum by (name, container_label_com_docker_compose_service) '
            '(rate(container_network_transmit_bytes_total{image!="",name!=""}[5m]))'
        ),
    }
    rows: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=3.0) as client:
            responses = await asyncio.gather(*(
                client.get("/api/v1/query", params={"query": expression})
                for expression in expressions.values()
            ))
        for (metric, _), response in zip(expressions.items(), responses, strict=True):
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError("Prometheus returned an unsuccessful query")
            for sample in payload.get("data", {}).get("result", []):
                labels = sample.get("metric", {})
                name = labels.get("name")
                if not name:
                    continue
                row = rows.setdefault(name, {
                    "name": name,
                    "service": labels.get("container_label_com_docker_compose_service") or name,
                    "cpu_cores": None,
                    "memory_bytes": None,
                    "network_rx_bytes_s": None,
                    "network_tx_bytes_s": None,
                })
                value = float(sample["value"][1])
                if math.isfinite(value):
                    row[metric] = value
                row["timestamp"] = float(sample["value"][0])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        return {"available": False, "containers": [], "error": str(exc)}
    return {
        "available": bool(rows),
        "containers": sorted(rows.values(), key=lambda item: item["name"].lower()),
        "count": len(rows),
    }

@router.get("/metrics", tags=["system"], include_in_schema=False)
def metrics(state: ApiState = Depends(get_state)) -> Response:
    return Response(generate_latest(state.registry), media_type=CONTENT_TYPE_LATEST)
