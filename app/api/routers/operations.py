from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from docker.errors import APIError, DockerException, NotFound
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import docker
from app.api.deps import get_state
from app.api.state import ApiState

router = APIRouter(prefix="/v1/ops", tags=["container-operations"])
log = logging.getLogger("predictiveops.operations")
PROJECT = "predictiveops"
STRESS_IMAGE = "python:3.12-alpine"
STRESS_CODE = "import time\nblocks=[]\nwhile True:\n blocks.append(bytearray(1024*1024))\n time.sleep(.25)"
WATCHERS: dict[str, tuple[threading.Event, threading.Thread]] = {}


def _db_path(state: ApiState) -> Path:
    path = Path(state.settings.sqlite_path).with_name("operations.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(path: Path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS incidents (
        id TEXT PRIMARY KEY, container_id TEXT NOT NULL, container_name TEXT NOT NULL,
        service TEXT, event TEXT NOT NULL, exit_code INTEGER, oom_killed INTEGER NOT NULL DEFAULT 0,
        error TEXT, started_at TEXT, finished_at TEXT NOT NULL, captured_at TEXT NOT NULL,
        logs TEXT NOT NULL DEFAULT '', report TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS stress_runs (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, max_users INTEGER NOT NULL,
        users_started INTEGER NOT NULL DEFAULT 0, failure TEXT, started_at TEXT NOT NULL,
        finished_at TEXT, worker_ids TEXT NOT NULL DEFAULT '[]'
    )""")
    db.commit()
    return db


def _client():
    return docker.from_env(timeout=8)


def _project_container(container):
    return container.labels.get("com.docker.compose.project") == PROJECT


def _capture_incident(
    path: Path, container_id: str, event: str = "die",
    event_data: dict | None = None,
) -> None:
    try:
        container = _client().containers.get(container_id)
        if not _project_container(container):
            return
        container.reload()
        labels = container.labels
        if labels.get("predictiveops.role") == "stress" and event != "stress_failure":
            return

        state = container.attrs.get("State", {})
        actor = (event_data or {}).get("Actor", {})
        attributes = actor.get("Attributes", {})
        exit_code = state.get("ExitCode")
        if attributes.get("exitCode") is not None:
            try:
                event_exit_code = int(attributes["exitCode"])
                if event_exit_code != 0 or exit_code in (None, 0):
                    exit_code = event_exit_code
            except (TypeError, ValueError):
                pass
        oom_killed = bool(state.get("OOMKilled") or event == "oom")
        error = state.get("Error") or attributes.get("error") or ""
        if event in {"die", "oom"} and exit_code == 0 and not oom_killed and not error:
            return

        finished = state.get("FinishedAt")
        if not finished or finished.startswith("0001-"):
            event_payload = event_data or {}
            if event_payload.get("timeNano"):
                event_timestamp = float(event_payload["timeNano"]) / 1_000_000_000
            else:
                event_timestamp = float(event_payload["time"]) if event_payload.get("time") else None
            finished = datetime.fromtimestamp(event_timestamp, UTC).isoformat() if event_timestamp else datetime.now(UTC).isoformat()
        started = state.get("StartedAt")
        runtime_seconds = None
        if started and finished and not started.startswith("0001-") and not finished.startswith("0001-"):
            try:
                runtime_seconds = max(0.0, (datetime.fromisoformat(finished.replace("Z", "+00:00")) -
                                            datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds())
            except ValueError:
                pass

        name = container.name or attributes.get("name") or container_id[:12]
        service = labels.get("com.docker.compose.service")
        try:
            logs = container.logs(tail=250, timestamps=True, stdout=True, stderr=True).decode("utf-8", errors="replace").strip()
        except (DockerException, OSError) as exc:
            logs = f"Container logs could not be read after exit: {exc}"
        if not logs:
            logs = "Docker retained no stdout/stderr lines for this incident. Use exit code, OOM state, Docker error and container metadata below for diagnosis."

        if oom_killed or exit_code == 137:
            diagnosis = "The container was terminated by the kernel or Docker due to memory pressure (OOM/SIGKILL)."
        elif error:
            diagnosis = f"Docker reported a runtime error: {error}"
        elif exit_code not in (None, 0):
            diagnosis = f"The container process exited unsuccessfully with exit code {exit_code}."
        elif event == "stress_failure":
            diagnosis = "A stress-test worker exited before the test completed; this marks the measured capacity boundary."
        else:
            diagnosis = "Docker reported a container termination. Review the captured logs and state metadata."

        config = container.attrs.get("Config", {})
        report = {
            "incident_id": f"{container.id[:12]}-{finished}",
            "container_id": container.id,
            "container_name": name,
            "compose_service": service,
            "image": config.get("Image") or attributes.get("image"),
            "event": event,
            "status": state.get("Status"),
            "exit_code": exit_code,
            "oom_killed": oom_killed,
            "error": error,
            "diagnosis": diagnosis,
            "started_at": started,
            "finished_at": finished,
            "runtime_seconds": round(runtime_seconds, 2) if runtime_seconds is not None else None,
            "restart_count": container.attrs.get("RestartCount", 0),
            "captured_at": datetime.now(UTC).isoformat(),
            "logs_tail": logs,
            "docker_event": attributes,
        }
        with _connect(path) as db:
            db.execute("""INSERT OR IGNORE INTO incidents
                (id,container_id,container_name,service,event,exit_code,oom_killed,error,
                 started_at,finished_at,captured_at,logs,report)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report["incident_id"], report["container_id"], name, service, event,
                 exit_code, int(oom_killed), error, started, finished,
                 report["captured_at"], logs, json.dumps(report)))
    except (DockerException, NotFound, OSError, sqlite3.Error) as exc:
        log.warning("incident capture failed for %s: %s", container_id[:12], exc)

def start_event_watcher(path: Path) -> None:
    key = str(path)
    if key in WATCHERS and WATCHERS[key][1].is_alive():
        return
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            try:
                client = _client()
                events = client.events(
                    decode=True, filters={"type": "container", "event": ["die", "oom"]}
                )
                for event in events:
                    if stop.is_set():
                        break
                    actor = event.get("Actor", {}).get("ID")
                    action = event.get("Action", event.get("status", "die"))
                    if actor:
                        _capture_incident(path, actor, action, event)
            except DockerException as exc:
                log.warning("Docker event stream unavailable: %s", exc)
                stop.wait(5)

    thread = threading.Thread(target=watch, name="container-event-watcher", daemon=True)
    WATCHERS[key] = (stop, thread)
    thread.start()


def stop_event_watcher(path: Path) -> None:
    watcher = WATCHERS.pop(str(path), None)
    if watcher:
        watcher[0].set()


class StressRequest(BaseModel):
    max_users: int = Field(default=10, ge=1, le=10)
    duration_seconds: int = Field(default=30, ge=5, le=60)


def _find_container(container_id: str):
    client = _client()
    try:
        container = client.containers.get(container_id)
    except NotFound:
        raise HTTPException(status_code=404, detail="Container not found") from None
    if not _project_container(container):
        raise HTTPException(status_code=403, detail="Only PredictiveOps project containers can be managed")
    return client, container


@router.get("/containers")
def managed_containers():
    try:
        client = _client()
        items = []
        for c in client.containers.list(all=True, filters={"label": f"com.docker.compose.project={PROJECT}"}):
            c.reload()
            st = c.attrs.get("State", {})
            service = c.labels.get("com.docker.compose.service", "—")
            items.append({
                "id": c.id, "short_id": c.short_id, "name": c.name, "service": service,
                "status": c.status, "created": c.attrs.get("Created"),
                "exit_code": st.get("ExitCode"), "oom_killed": bool(st.get("OOMKilled")),
                "error": st.get("Error") or "", "can_control": service != "api",
            })
        return {"available": True, "containers": sorted(items, key=lambda x: x["name"])}
    except DockerException as exc:
        return {"available": False, "containers": [], "error": str(exc)}


@router.post("/containers/{container_id}/{action}")
def container_action(container_id: str, action: str):
    if action not in {"start", "restart", "stop", "kill"}:
        raise HTTPException(status_code=400, detail="Action must be start, restart, stop, or kill")
    _, container = _find_container(container_id)
    service = container.labels.get("com.docker.compose.service")
    if service == "api":
        raise HTTPException(status_code=409, detail="The API container cannot control itself")
    try:
        if action == "start":
            container.start()
        elif action == "restart":
            container.restart(timeout=5)
        elif action == "stop":
            container.stop(timeout=5)
        else:
            container.kill()
        log.info(
            "container action",
            extra={"container": container.name, "service": service, "action": action},
        )
        return {"success": True, "container": container.name, "action": action}
    except (DockerException, APIError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/containers/{container_id}/logs")
def container_logs(container_id: str, tail: int = 250, since: int = 0):
    _, container = _find_container(container_id)
    try:
        # Docker SDK rejects integer zero for since; None means no time filter.
        output = container.logs(tail=max(1, min(tail, 2000)),
                                since=since if since > 0 else None,
                                timestamps=True, stdout=True, stderr=True)
        return {"container": container.name, "logs": output.decode("utf-8", errors="replace")}
    except DockerException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/incidents")
def incidents(limit: int = 100, state: ApiState = Depends(get_state)):
    try:
        with _connect(_db_path(state)) as db:
            rows = db.execute("SELECT report FROM incidents ORDER BY captured_at DESC LIMIT ?",
                              (max(1, min(limit, 500)),)).fetchall()
        return {"incidents": [json.loads(row["report"]) for row in rows], "count": len(rows)}
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/stress")
def start_stress(request: StressRequest, state: ApiState = Depends(get_state)):
    run_id = uuid.uuid4().hex[:12]
    path = _db_path(state)
    started = datetime.now(UTC).isoformat()
    with _connect(path) as db:
        db.execute("INSERT INTO stress_runs(id,status,max_users,started_at) VALUES(?,?,?,?)",
                   (run_id, "running", request.max_users, started))
    thread = threading.Thread(
        target=_run_stress, args=(path, run_id, request.max_users, request.duration_seconds),
        name=f"stress-{run_id}", daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "running", "max_users": request.max_users,
            "duration_seconds": request.duration_seconds}


def _run_stress(path: Path, run_id: str, max_users: int, duration: int):
    client = None
    workers = []
    failure = None
    started = time.monotonic()
    status = "completed"
    try:
        client = _client()
        client.images.pull(STRESS_IMAGE)
        for index in range(1, max_users + 1):
            if time.monotonic() - started >= duration:
                status = "timed_out"
                break
            worker = client.containers.run(
                STRESS_IMAGE, ["python", "-c", STRESS_CODE], detach=True,
                name=f"predictiveops-stress-{run_id}-{index}",
                mem_limit="32m", nano_cpus=100_000_000, pids_limit=8,
                network_disabled=True, read_only=True, tmpfs={"/tmp": "rw,noexec,nosuid,size=1m"},
                labels={"com.docker.compose.project": PROJECT, "predictiveops.role": "stress",
                        "predictiveops.stress_run": run_id, "predictiveops.virtual_user": str(index)},
            )
            workers.append(worker)
            with _connect(path) as db:
                db.execute("UPDATE stress_runs SET users_started=?,worker_ids=? WHERE id=?",
                           (len(workers), json.dumps([w.id for w in workers]), run_id))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                for active_index, active in enumerate(workers, start=1):
                    active.reload()
                    if active.status != "running":
                        failure = f"Virtual user {active_index} exited ({active.status}, exit code {active.attrs.get('State', {}).get('ExitCode', 'unknown')})"
                        status = "capacity_reached"
                        _capture_incident(path, active.id, "stress_failure")
                        break
                if failure:
                    break
                time.sleep(.2)
            if failure:
                break
        if not failure and len(workers) == max_users:
            status = "limit_reached"
    except Exception as exc:
        failure = str(exc)
        status = "error"
        log.exception("stress run %s failed", run_id)
    finally:
        for worker in workers:
            try:
                worker.stop(timeout=1)
            except Exception:
                pass
            try:
                worker.remove(force=True)
            except Exception:
                pass
        with _connect(path) as db:
            db.execute("UPDATE stress_runs SET status=?,failure=?,finished_at=? WHERE id=?",
                       (status, failure, datetime.now(UTC).isoformat(), run_id))


@router.get("/stress/{run_id}")
def stress_status(run_id: str, state: ApiState = Depends(get_state)):
    with _connect(_db_path(state)) as db:
        row = db.execute("SELECT * FROM stress_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Stress run not found")
    return dict(row)


@router.get("/stress")
def stress_history(limit: int = 20, state: ApiState = Depends(get_state)):
    with _connect(_db_path(state)) as db:
        rows = db.execute("SELECT * FROM stress_runs ORDER BY started_at DESC LIMIT ?",
                          (max(1, min(limit, 100)),)).fetchall()
    return {"runs": [dict(row) for row in rows]}
