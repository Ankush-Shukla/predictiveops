# ruff: noqa: E501, E701, E702, E401, I001
from __future__ import annotations
import asyncio, ipaddress, math, statistics, time, uuid
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

app = FastAPI(title="PredictiveOps isolated load generator")
registry = CollectorRegistry()
error_metric = Gauge("predictiveops_load_test_error_rate", "Latest completed load-test error ratio", ["service"], registry=registry)
latency_metric = Gauge("predictiveops_load_test_latency_p95_seconds", "Latest completed load-test p95 latency", ["service"], registry=registry)
capacity_metric = Gauge("predictiveops_load_test_capacity_level", "Latest load-test capacity status (0 normal, 1 warning, 2 critical)", ["service"], registry=registry)
jobs: dict[str, dict] = {}
tasks: dict[str, asyncio.Task] = {}

class LoadJob(BaseModel):
    service: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    target_ip: str
    port: int = Field(ge=1, le=65535)
    path: str = Field(default="/health", min_length=1, max_length=512)
    concurrency: int = Field(default=5, ge=1, le=100)
    request_rate: int = Field(default=10, ge=1, le=100)
    duration_seconds: int = Field(default=30, ge=5, le=300)
    warning_latency_ms: float = Field(default=500, ge=1, le=60000)
    critical_latency_ms: float = Field(default=1500, ge=1, le=120000)
    warning_error_rate: float = Field(default=0.02, ge=0, le=1)
    critical_error_rate: float = Field(default=0.10, ge=0, le=1)

    @field_validator("target_ip")
    @classmethod
    def private_target_only(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("target_ip must be a literal Docker-network IP address") from exc
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("target_ip must be a private Docker-network IPv4 address")
        return str(address)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "://" in value or any(ord(c) < 32 for c in value):
            raise ValueError("path must be an absolute URL path, not a URL or authority")
        return value

def percentile(values: list[float], fraction: float) -> float | None:
    if not values: return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)], 3)

async def execute(job_id: str, config: LoadJob) -> None:
    record = jobs[job_id]
    latencies: list[float] = []
    failures = completed = 0
    status_codes: dict[str, int] = {}
    semaphore = asyncio.Semaphore(config.concurrency)
    deadline = time.monotonic() + config.duration_seconds
    interval = 1.0 / config.request_rate
    target = f"http://{config.target_ip}:{config.port}{config.path}"
    pending: set[asyncio.Task] = set()

    async def request_once(client: httpx.AsyncClient) -> None:
        nonlocal failures, completed
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await client.get(target)
                latencies.append((time.perf_counter() - started) * 1000)
                code = response.status_code
                status_codes[str(code)] = status_codes.get(str(code), 0) + 1
                if code >= 400: failures += 1
            except httpx.HTTPError:
                latencies.append((time.perf_counter() - started) * 1000)
                failures += 1
                status_codes["transport_error"] = status_codes.get("transport_error", 0) + 1
            finally:
                completed += 1
                record.update({"completed_requests": completed, "failed_requests": failures})

    record["status"] = "running"
    try:
        limits = httpx.Limits(max_connections=config.concurrency, max_keepalive_connections=config.concurrency)
        async with httpx.AsyncClient(timeout=5.0, limits=limits, trust_env=False, follow_redirects=False) as client:
            next_request = time.monotonic()
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now < next_request:
                    await asyncio.sleep(min(next_request - now, 0.05))
                    continue
                pending.add(asyncio.create_task(request_once(client)))
                pending = {task for task in pending if not task.done()}
                next_request += interval
                if len(pending) >= config.concurrency:
                    _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if next_request < now - interval: next_request = now + interval
            if pending: await asyncio.gather(*pending)
        error_ratio = failures / completed if completed else 1.0
        p95 = percentile(latencies, 0.95)
        p50, p99 = percentile(latencies, 0.50), percentile(latencies, 0.99)
        latency_ms = p95 or 0
        level = 2 if error_ratio >= config.critical_error_rate or latency_ms >= config.critical_latency_ms else (1 if error_ratio >= config.warning_error_rate or latency_ms >= config.warning_latency_ms else 0)
        record.update({"status":"completed","completed_requests":completed,"failed_requests":failures,"error_rate":round(error_ratio,4),"requests_per_second":round(completed/config.duration_seconds,2),"latency_ms":{"p50":p50,"p95":p95,"p99":p99,"mean":round(statistics.fmean(latencies),3) if latencies else None},"status_codes":status_codes,"capacity_level":level,"finished_at":time.time()})
        error_metric.labels(config.service).set(error_ratio)
        latency_metric.labels(config.service).set((p95 or 0)/1000)
        capacity_metric.labels(config.service).set(level)
    except asyncio.CancelledError:
        for pending_task in pending:
            pending_task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        record.update({"status": "cancelled", "finished_at": time.time(), "completed_requests": completed, "failed_requests": failures})
        raise
    except Exception as exc:
        record.update({"status":"failed","error":f"{type(exc).__name__}: {exc}","finished_at":time.time()})

@app.get("/health")
def health() -> dict: return {"status":"ok"}

@app.post("/jobs", status_code=202)
async def start_job(config: LoadJob) -> dict:
    if config.critical_latency_ms < config.warning_latency_ms: raise HTTPException(422,"critical latency threshold must be >= warning threshold")
    if config.critical_error_rate < config.warning_error_rate: raise HTTPException(422,"critical error threshold must be >= warning threshold")
    if any(item.get("status") in {"queued","running"} for item in jobs.values()): raise HTTPException(409,"A load test is already running")
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"job_id":job_id,"service":config.service,"target":f"{config.target_ip}:{config.port}{config.path}","status":"queued","concurrency":config.concurrency,"request_rate":config.request_rate,"duration_seconds":config.duration_seconds,"completed_requests":0,"failed_requests":0,"started_at":time.time()}
    tasks[job_id] = asyncio.create_task(execute(job_id, config))
    return jobs[job_id]

@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    if job_id not in jobs: raise HTTPException(404,"Load test not found")
    return jobs[job_id]

@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    task = tasks.get(job_id)
    if task is None or task.done(): raise HTTPException(409,"Load test is not running")
    task.cancel()
    return {"job_id":job_id,"status":"cancelling"}

@app.get("/metrics")
def metrics() -> Response: return Response(generate_latest(registry),media_type=CONTENT_TYPE_LATEST)
