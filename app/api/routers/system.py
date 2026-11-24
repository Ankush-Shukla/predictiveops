from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import get_state, production_model_block_reason
from app.api.state import ApiState
from app.core.exceptions import ModelNotReadyError
from app.models.predictor import Predictor

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
            scope: {"step_seconds": poller.step, "services": poller.status}
            for scope, poller in sorted(pollers.items())
        }}}
    poller = request.app.state.poller
    if poller is None:
        return {"prometheus": {"enabled": False}}
    return {"prometheus": {"enabled": True, "step_seconds": poller.step, "services": poller.status}}


@router.get("/metrics", tags=["system"], include_in_schema=False)
def metrics(state: ApiState = Depends(get_state)) -> Response:
    return Response(generate_latest(state.registry), media_type=CONTENT_TYPE_LATEST)
