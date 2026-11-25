"""POC Docker operations, stress testing, and persistent crash reports."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import docker
from docker.errors import DockerException, NotFound
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/v1/ops", tags=["operations"])
DB_PATH = Path(os.getenv("PREDICTIVEOPS_OPS_DB", "/srv/artifacts/ops_incidents.sqlite"))
PROJECT = os.getenv("PREDICTIVEOPS_DOCKER_PROJECT", "predictiveops")
STRESS_IMAGE = os.getenv("PREDICTIVEOPS_STRESS_IMAGE", "predictiveops-runtime:local")


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            container_id TEXT NOT NULL,
            container_name TEXT NOT NULL,
            service TEXT,
            reason TEXT NOT NULL,
            exit_code INTEGER,
            oom_killed INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            captured_at TEXT NOT NULL,
            logs TEXT NOT NULL DEFAULT '',
            UNIQUE(container_id, finished_at)
        )
    """)
    conn.commit()
    return conn


def _client():
    try:
        return docker.from_env()
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker connection unavailable: {exc}") from exc


def _managed(container) -> bool:
    labels = container.labels or {}
    return labels.get("com.docker.compose.project") == PROJECT or labels.get("predictiveops.stress") == "true"


def _capture_failure(container) -> None:
    try:
        container.reload()
        state = container.attrs.get("State", {})
        exit_code = state.get("ExitCode")
        oom = bool(state.get("OOMKilled"))
        error = state.get("Error") or ""
        if container.status not in ("exited", "dead") or (exit_code == 0 and not oom and not error):
            return
        finished = state.get("FinishedAt") or datetime.now(UTC).isoformat()
        started = state.get("StartedAt")
        reason = error or ("Container was OOM-killed" if oom else f"Container exited with code {exit_code}")
        try:
            logs = container.logs(stdout=True, stderr=True, tail=300, timestamps=True).decode("utf-8", "replace")
        except DockerException as exc:
            logs = f"Unable to retrieve container logs: {exc}"
        conn = _db()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO incidents
                (container_id, container_name, service, reason, exit_code, oom_killed,
                 started_at, finished_at, captured_at, logs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (container.id, container.name, (container.labels or {}).get("com.docker.compose.service"),
                 reason, exit_code, int(oom), started, finished, datetime.now(UTC).isoformat(), logs),
            )
            conn.commit()
        finally:
            conn.close()
    except (DockerException, KeyError, TypeError):
        return


def _container_payload(container) -> dict:
    container.reload()
    state = container.attrs.get("State", {})
    labels = container.labels or {}
    is_stress = labels.get("predictiveops.stress") == "true"
    payload = {
        "id": container.id,
        "short_id": container.short_id,
        "name": container.name,
        "service": labels.get("com.docker.compose.service", "stress-test" if is_stress else "—"),
        "status": container.status,
        "state": state.get("Status", container.status),
        "exit_code": state.get("ExitCode"),
        "oom_killed": bool(state.get("OOMKilled")),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "stress_test": is_stress,
        "managed": _managed(container),
    }
    if is_stress:
        try:
            log_text = container.logs(stdout=True, stderr=True, tail=100).decode("utf-8", "replace")
            counts = re.findall(r"SIMULATED_USERS_STARTED=(\d+)", log_text)
            payload["simulated_users_started"] = int(counts[-1]) if counts else 0
            payload["stress_result"] = (
                "running" if container.status == "running"
                else "crashed" if state.get("OOMKilled") or (state.get("ExitCode") not in (0, None))
                else "completed"
            )
        except DockerException:
            payload["simulated_users_started"] = 0
    return payload


@router.get("/containers")
def list_managed_containers() -> dict:
    client = _client()
    try:
        containers = [c for c in client.containers.list(all=True) if _managed(c)]
        rows = []
        for container in containers:
            _capture_failure(container)
            rows.append(_container_payload(container))
        return {"available": True, "containers": sorted(rows, key=lambda r: r["name"])}
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        client.close()


@router.post("/containers/{container_id}/action")
def container_action(container_id: str, payload: dict) -> dict:
    action = str(payload.get("action", "")).lower()
    if action not in {"start", "stop", "restart", "kill"}:
        raise HTTPException(status_code=400, detail="Action must be start, stop, restart, or kill")
    client = _client()
    try:
        container = client.containers.get(container_id)
        if not _managed(container) or (container.labels or {}).get("com.docker.compose.project") != PROJECT:
            raise HTTPException(status_code=403, detail="Only containers in this PredictiveOps Compose project can be controlled")
        if action == "start":
            container.start()
        elif action == "stop":
            container.stop(timeout=10)
        elif action == "restart":
            container.restart(timeout=10)
        else:
            container.kill()
        container.reload()
        return {"success": True, "action": action, "container": _container_payload(container)}
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Container not found") from exc
    except DockerException as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        client.close()


@router.get("/containers/{container_id}/logs")
def container_logs(container_id: str, tail: int = Query(200, ge=1, le=1000)) -> dict:
    client = _client()
    try:
        container = client.containers.get(container_id)
        if not _managed(container):
            raise HTTPException(status_code=403, detail="Container is outside this project")
        raw = container.logs(stdout=True, stderr=True, tail=tail, timestamps=True)
        return {"container_id": container.id, "container_name": container.name,
                "logs": raw.decode("utf-8", "replace"), "tail": tail}
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Container not found") from exc
    except DockerException as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        client.close()


@router.get("/incidents")
def incident_history(limit: int = Query(100, ge=1, le=500)) -> dict:
    # Refresh the snapshot first so recently exited containers are captured.
    list_managed_containers()
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"incidents": [dict(row) for row in rows], "count": len(rows)}
    finally:
        conn.close()


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: int) -> dict:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return dict(row)
    finally:
        conn.close()


@router.post("/stress/start")
def start_stress_test(payload: dict) -> dict:
    max_processes = max(1, min(int(payload.get("max_processes", 80)), 100))
    duration = max(10, min(int(payload.get("duration_seconds", 90)), 180))
    client = _client()
    # Each child represents one simulated concurrent user. Memory, CPU, PID count,
    # and wall-clock duration are bounded so the POC cannot intentionally exhaust the host.
    worker = r"""
import multiprocessing as mp, os, time
def user_worker():
    block = bytearray(4 * 1024 * 1024)
    for page in range(0, len(block), 4096): block[page] = 1
    n = 0
    end = time.time() + DURATION
    while time.time() < end:
        block[n % len(block)] = (block[n % len(block)] + 1) % 256
        n += 1
if __name__ == '__main__':
    mp.set_start_method('fork')
    workers = []
    deadline = time.time() + DURATION
    for _ in range(MAX_PROCESSES):
        if time.time() >= deadline: break
        try:
            p = mp.Process(target=user_worker)
            p.start()
            workers.append(p)
            print("SIMULATED_USERS_STARTED=" + str(len(workers)), flush=True)
        except (OSError, MemoryError) as exc:
            print("SPAWN_LIMIT_REACHED=" + repr(exc), flush=True)
            break
        time.sleep(0.25)
    while time.time() < deadline and any(p.is_alive() for p in workers):
        time.sleep(1)
    for p in workers:
        if p.is_alive(): p.terminate()
        p.join(timeout=2)
    print("STRESS_TEST_COMPLETED_USERS=" + str(len(workers)), flush=True)
"""
    worker = worker.replace("DURATION", str(duration)).replace("MAX_PROCESSES", str(max_processes))
    try:
        container = client.containers.run(
            STRESS_IMAGE, command=["python", "-c", worker], detach=True,
            name=f"predictiveops-stress-{int(time.time())}",
            labels={"com.docker.compose.project": PROJECT, "predictiveops.stress": "true",
                    "predictiveops.role": "load-test"},
            mem_limit="256m", memswap_limit="256m", nano_cpus=1_000_000_000,
            pids_limit=128, network_disabled=True, auto_remove=False,
        )
        return {"started": True, "container": _container_payload(container),
                "simulated_user_limit": max_processes, "duration_seconds": duration,
                "limits": {"memory": "256 MiB", "cpu": "1 core", "pids": 128}}
    except DockerException as exc:
        raise HTTPException(status_code=500, detail=f"Could not start stress test: {exc}") from exc
    finally:
        client.close()


@router.get("/stress/status")
def stress_status() -> dict:
    client = _client()
    try:
        rows = []
        for container in client.containers.list(all=True, filters={"label": "predictiveops.stress=true"}):
            _capture_failure(container)
            rows.append(_container_payload(container))
        return {"runs": sorted(rows, key=lambda r: r["name"], reverse=True)}
    finally:
        client.close()


@router.post("/stress/{container_id}/stop")
def stop_stress_test(container_id: str) -> dict:
    client = _client()
    try:
        container = client.containers.get(container_id)
        if (container.labels or {}).get("predictiveops.stress") != "true":
            raise HTTPException(status_code=403, detail="Not a PredictiveOps stress-test container")
        if container.status == "running":
            container.stop(timeout=3)
        container.reload()
        return {"success": True, "container": _container_payload(container)}
    except NotFound as exc:
        raise HTTPException(status_code=404, detail="Stress run not found") from exc
    except DockerException as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        client.close()
