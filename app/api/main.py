"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_error_handlers
from app.api.routers import policy, predict, simulate, system
from app.api.state import ApiState
from app.core.config import Settings, get_settings
from app.core.exceptions import PredictiveOpsError
from app.core.logging import configure_logging, get_logger
from app.models.predictor import Predictor

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
log = get_logger("app.api")


def create_app(settings: Settings | None = None, predictor: Predictor | None = None,
               *, load_model: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level, settings.log_json)
        if app.state.api.predictor is None and load_model:
            try:
                app.state.api.set_predictor(Predictor.from_champion(settings.model_dir))
                log.info("champion model loaded", extra={"version": app.state.api.predictor.version})
            except PredictiveOpsError as exc:
                log.warning("no champion model loaded", extra={"reason": str(exc)})
        yield

    app = FastAPI(title="PredictiveOps API", version="0.6.0", lifespan=lifespan,
                  description="Predicts the probability of service failure within the next "
                              "15-30 minutes. Demo data is SYNTHETIC.")
    app.state.api = ApiState(settings=settings, predictor=predictor)
    register_error_handlers(app)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        app.state.api.collectors.requests.labels(request.method, route, str(response.status_code)).inc()
        response.headers["x-request-id"] = rid
        log.info("request", extra={"request_id": rid, "method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return response

    app.include_router(system.router)
    app.include_router(predict.router)
    app.include_router(policy.router)
    app.include_router(simulate.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.api.main:app_factory
    return create_app()
