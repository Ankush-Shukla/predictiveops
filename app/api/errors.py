"""Map domain exceptions to HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    DataGenerationError,
    DataValidationError,
    ModelNotReadyError,
    PredictiveOpsError,
)
from app.core.logging import get_logger
from app.models.bundle import ModelNotFoundError

log = get_logger(__name__)


def _json(status: int, code: str, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": str(exc)})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DataValidationError)
    async def _validation(_: Request, exc: DataValidationError):
        return _json(422, "invalid_data", exc)

    @app.exception_handler(DataGenerationError)
    async def _gen(_: Request, exc: DataGenerationError):
        return _json(422, "invalid_scenario", exc)

    @app.exception_handler(ModelNotReadyError)
    @app.exception_handler(ModelNotFoundError)
    async def _not_ready(_: Request, exc: Exception):
        return _json(503, "model_not_ready", exc)

    @app.exception_handler(PredictiveOpsError)
    async def _domain(_: Request, exc: PredictiveOpsError):
        log.exception("unhandled domain error")
        return _json(500, "internal_error", exc)
