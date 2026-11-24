"""Demo target service (SIMULATED metrics) for the PredictiveOps compose stack.

Exposes Prometheus gauges ``demo_<metric>`` (one simulated minute per ``DEMO_SIM_MINUTE_SECONDS``
real seconds), a small real HTTP endpoint (/work) whose failures follow the simulated error rate,
and fault-injection endpoints protected by ``DEMO_ADMIN_TOKEN`` (disabled when unset).
"""

from __future__ import annotations

import hmac
import os
import random
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import DataGenerationError
from data.synthetic.generator import FAULT_TYPES
from demo_app.simulator import LiveSimulator


class FaultRequest(BaseModel):
    fault_type: str = Field(pattern=r"^[a-z_]{3,30}$")
    ramp_minutes: int = Field(default=45, ge=5, le=180)


def create_demo_app(sim_minute_seconds: float | None = None, autostart: bool = True,
                    admin_token: str | None = None, seed: int | None = None) -> FastAPI:
    step = sim_minute_seconds if sim_minute_seconds is not None else float(
        os.getenv("DEMO_SIM_MINUTE_SECONDS", "5"))
    token = admin_token if admin_token is not None else (os.getenv("DEMO_ADMIN_TOKEN") or None)
    sim = LiveSimulator(seed=seed if seed is not None else int(os.getenv("DEMO_SEED", "42")))
    reg = CollectorRegistry()
    gauges = {m: Gauge(f"demo_{m}", f"SIMULATED {m}", registry=reg) for m in METRIC_COLUMNS}
    fault_g = Gauge("demo_fault_active", "1 while a fault is injected", ["fault_type"], registry=reg)
    minutes = Counter("demo_sim_minutes_total", "Simulated minutes elapsed", registry=reg)
    http = Counter("demo_http_requests_total", "Requests to /work", ["status"], registry=reg)
    stop = threading.Event()

    def publish() -> None:
        vals = sim.step()
        for m, v in vals.items():
            gauges[m].set(v)
        st = sim.state()
        for f in FAULT_TYPES:
            fault_g.labels(fault_type=f).set(1 if st["fault"] == f else 0)
        minutes.inc()

    def loop() -> None:
        while not stop.wait(step):
            publish()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        publish()  # metrics are available immediately
        thread = None
        if autostart:
            thread = threading.Thread(target=loop, daemon=True, name="demo-sim")
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=2)

    app = FastAPI(title="PredictiveOps demo target", version="0.1.0", lifespan=lifespan)
    app.state.sim, app.state.publish = sim, publish

    def admin(x_admin_token: str | None = Header(default=None)) -> None:
        if not token:
            raise HTTPException(403, "fault injection is disabled (set DEMO_ADMIN_TOKEN)")
        if x_admin_token is None or not hmac.compare_digest(x_admin_token, token):
            raise HTTPException(401, "invalid or missing X-Admin-Token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(reg), media_type=CONTENT_TYPE_LATEST)

    @app.get("/work")
    def work(response: Response) -> dict:
        """A real endpoint: fails with probability equal to the current simulated error rate."""
        failed = random.random() < gauges["error_rate"]._value.get()  # noqa: SLF001
        status = 500 if failed else 200
        http.labels(status=str(status)).inc()
        if failed:
            raise HTTPException(500, "simulated failure")
        return {"ok": True}

    @app.get("/state")
    def state() -> dict:
        return sim.state()

    @app.post("/fault", dependencies=[Depends(admin)])
    def inject(req: FaultRequest) -> dict:
        try:
            sim.inject(req.fault_type, req.ramp_minutes)
        except DataGenerationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return sim.state()

    @app.post("/fault/clear", dependencies=[Depends(admin)])
    def clear() -> dict:
        sim.clear()
        return sim.state()

    return app


def app_factory() -> FastAPI:  # uvicorn --factory demo_app.main:app_factory
    return create_demo_app()
