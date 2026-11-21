"""FastAPI dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from app.api.state import ApiState
from app.core.exceptions import ModelNotReadyError
from app.models.predictor import Predictor


def get_state(request: Request) -> ApiState:
    return request.app.state.api


def get_predictor(request: Request) -> Predictor:
    p = request.app.state.api.predictor
    if p is None:
        raise ModelNotReadyError("no champion model loaded; train and promote one first "
                                 "(python scripts/train.py --promote)")
    return p


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared-secret protection for write endpoints (enabled when API_KEY is set)."""
    secret = request.app.state.api.settings.api_key
    if secret is None:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
