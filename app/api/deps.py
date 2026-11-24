"""FastAPI dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from app.api.state import ApiState
from app.core.exceptions import ModelNotReadyError
from app.models.predictor import Predictor


def production_model_block_reason(state: ApiState, predictor: Predictor | None = None,
                                  expected_scope: str | None = None) -> str | None:
    """Reject demo-trained, mismatched-scope or ungated models in production."""
    predictor = predictor or state.predictor
    if predictor is None:
        return "no champion model loaded"
    if state.settings.env != "production":
        return None
    metadata = predictor.bundle.metadata
    if expected_scope is not None and metadata.get("scope") != expected_scope:
        return f"loaded model scope does not match required {expected_scope!r} scope"
    source = str(metadata.get("data_source", "unknown")).lower().split("+")
    if "synthetic" in source or "unknown" in source:
        return "model was not trained exclusively on identified production data"
    gates = (metadata.get("evaluation") or {}).get("gates")
    if not isinstance(gates, dict) or gates.get("passed") is not True:
        return "model has no passing production quality-gate report"
    return None


def get_state(request: Request) -> ApiState:
    return request.app.state.api


def get_predictor(request: Request) -> Predictor:
    state = request.app.state.api
    p = state.predictor
    reason = production_model_block_reason(state)
    if p is None or reason is not None:
        raise ModelNotReadyError(reason or "model is not ready")
    return p


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared-secret protection for write endpoints (enabled when API_KEY is set)."""
    secret = request.app.state.api.settings.api_key
    if secret is None:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
