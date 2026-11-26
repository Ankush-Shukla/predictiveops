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
from app.api.poller import PrometheusPoller
from app.api.routers import (
    canary,
    history,
    load_testing,
    log_stream,
    operations,
    policy,
    predict,
    simulate,
    system,
)
from app.api.state import ApiState
from app.core.config import Settings, get_settings
from app.core.domain import APPLICATION_METRIC_COLUMNS, HOST_METRIC_COLUMNS
from app.core.exceptions import PredictiveOpsError
from app.core.logging import configure_logging, get_logger
from app.models.predictor import Predictor
from app.services.prometheus_client import (
    APPLICATION_QUERIES,
    HOST_QUERIES,
    PrometheusClient,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
log = get_logger("app.api")


def create_app(settings: Settings | None = None, predictor: Predictor | None = None,
               *, load_model: bool = True, prometheus_client: PrometheusClient | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level, settings.log_json)
        if load_model:
            if settings.env == "production":
                loaded: dict[str, Predictor] = {}
                for scope in ("host", "application"):
                    try:
                        loaded[scope] = Predictor.from_champion(settings.model_dir / scope)
                        log.info(
                            "scope model loaded",
                            extra={"scope": scope, "version": loaded[scope].version},
                        )
                    except PredictiveOpsError as exc:
                        log.info("scope model unavailable", extra={"scope": scope, "reason": str(exc)})
                app.state.api.set_scope_predictors(loaded)

                # Isolated model used exclusively by the explicitly labelled demo endpoint.
                try:
                    demo = Predictor.from_champion(settings.model_dir)
                    metadata = demo.bundle.metadata
                    if (metadata.get("data_source") == "synthetic"
                            and metadata.get("scope", "combined") == "combined"):
                        app.state.api.set_demo_predictor(demo)
                        log.info("synthetic demonstration model loaded",
                                 extra={"version": demo.version})
                    else:
                        log.warning("root model rejected for demo: unexpected provenance or scope")
                except PredictiveOpsError as exc:
                    log.info("synthetic demonstration model unavailable",
                                extra={"reason": str(exc)})

                # Separate experimental model. Never register it as a production scope.
                try:
                    experimental = Predictor.from_champion(
                        settings.model_dir / "experimental-host"
                    )
                    metadata = experimental.bundle.metadata
                    if (
                        metadata.get("scope") == "host"
                        and metadata.get("data_source") == "synthetic"
                    ):
                        app.state.api.set_experimental_host_predictor(experimental)
                        log.info(
                            "experimental host model loaded",
                            extra={
                                "version": experimental.version,
                                "data_source": "synthetic",
                            },
                        )
                    else:
                        log.warning(
                            "experimental host model rejected: unexpected scope or provenance"
                        )
                except PredictiveOpsError as exc:
                    log.info(
                        "experimental host model unavailable",
                        extra={"reason": str(exc)},
                    )

            elif app.state.api.predictor is None:
                try:
                    app.state.api.set_predictor(Predictor.from_champion(settings.model_dir))
                    log.info("champion model loaded", extra={"version": app.state.api.predictor.version})
                except PredictiveOpsError as exc:
                    log.info("no champion model loaded", extra={"reason": str(exc)})

        pollers: list[PrometheusPoller] = []
        if settings.prometheus_enabled:
            if settings.env == "production":
                host_client = PrometheusClient(
                    settings.prometheus_url, queries=HOST_QUERIES,
                    required_metrics=HOST_METRIC_COLUMNS)
                host_poller = PrometheusPoller(
                    app.state.api, host_client, [settings.prometheus_host_series_id],
                    step_seconds=settings.prometheus_step_seconds,
                    interval_seconds=settings.prometheus_poll_seconds,
                    lookback_steps=settings.prometheus_lookback_steps,
                    scope="host")
                pollers.append(host_poller)
                if settings.prometheus_application_service_list:
                    app_client = PrometheusClient(
                        settings.prometheus_url, queries=APPLICATION_QUERIES,
                        required_metrics=APPLICATION_METRIC_COLUMNS)
                    pollers.append(PrometheusPoller(
                        app.state.api, app_client, settings.prometheus_application_service_list,
                        step_seconds=settings.prometheus_step_seconds,
                        interval_seconds=settings.prometheus_poll_seconds,
                        lookback_steps=settings.prometheus_lookback_steps,
                        scope="application"))
            elif settings.prometheus_service_list:
                client = prometheus_client or PrometheusClient(settings.prometheus_url)
                pollers.append(PrometheusPoller(
                    app.state.api, client, settings.prometheus_service_list,
                    step_seconds=settings.prometheus_step_seconds,
                    interval_seconds=settings.prometheus_poll_seconds,
                    lookback_steps=settings.prometheus_lookback_steps))

            app.state.pollers = {p.scope: p for p in pollers}
            app.state.poller = pollers[0] if len(pollers) == 1 else None
            for poller in pollers:
                poller.start()
        operations.cleanup_orphaned_stress_containers()
        operations.recover_interrupted_stress_runs(
            settings.sqlite_path.with_name("operations.sqlite3"), app.state.api.store
        )
        operations.start_event_watcher(
            settings.sqlite_path.with_name('operations.sqlite3'),
            settings.managed_container_name_list,
        )
        yield
        operations.stop_event_watcher(settings.sqlite_path.with_name('operations.sqlite3'))
        for poller in pollers:
            poller.stop()

    app = FastAPI(title="PredictiveOps API", version="0.8.0", lifespan=lifespan,
                  description="Scope-specific host and application failure-risk inference. "
                              "Production requires independently qualified real-data models.")
    app.state.api = ApiState(settings=settings, predictor=predictor)
    app.state.poller = None
    app.state.pollers = {}
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
    app.include_router(operations.router)
    app.include_router(log_stream.router)
    app.include_router(load_testing.router)
    app.include_router(history.router)
    app.include_router(predict.router)
    app.include_router(policy.router)
    if settings.env != "production":
        app.include_router(predict.development_router)
        app.include_router(canary.router)

    # Synthetic simulation is a development/demo surface, never a production API.
    if settings.env != "production":
        app.include_router(simulate.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.api.main:app_factory
    return create_app()
