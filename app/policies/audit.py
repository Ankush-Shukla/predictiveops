"""SQLite audit trail: alerts and every remediation decision (executed or not)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, series_id TEXT NOT NULL,
  level TEXT NOT NULL, probability REAL NOT NULL, threshold REAL NOT NULL,
  model_version TEXT, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, series_id TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT, outcome TEXT NOT NULL, dry_run INTEGER NOT NULL,
  probability REAL, model_version TEXT, reason TEXT, details TEXT);
CREATE INDEX IF NOT EXISTS ix_audit_target_ts ON audit_log(target, ts);
"""
# Outcomes that count against cooldown / rate limits (dry-run counts, so limits behave identically).
COUNTED_OUTCOMES = ("executed", "dry_run")


def _iso(ts: datetime) -> str:
    ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    return ts.isoformat(timespec="seconds")


class AuditStore:
    """Thread-safe SQLite store (single connection + lock). Use ':memory:' for ephemeral stores."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        p = str(path)
        if p != ":memory:":
            Path(p).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(p, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if p != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)

    def add_alert(self, *, ts: datetime, series_id: str, level: str, probability: float,
                  threshold: float, model_version: str | None, message: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO alerts(ts,series_id,level,probability,threshold,model_version,message)"
                " VALUES(?,?,?,?,?,?,?)",
                (_iso(ts), series_id, level, probability, threshold, model_version, message))

    def add_audit(self, *, ts: datetime, series_id: str, action: str, target: str | None, outcome: str,
                  dry_run: bool, probability: float | None, model_version: str | None,
                  reason: str | None, details: dict | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO audit_log(ts,series_id,action,target,outcome,dry_run,probability,"
                "model_version,reason,details) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (_iso(ts), series_id, action, target, outcome, int(dry_run), probability, model_version,
                 reason, json.dumps(details or {})))

    def _rows(self, table: str, limit: int, series_id: str | None) -> list[dict]:
        q, args = f"SELECT * FROM {table}", []  # table name is internal, never user input
        if series_id:
            q += " WHERE series_id=?"
            args.append(series_id)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._db.execute(q, args).fetchall()]

    def list_alerts(self, limit: int = 50, series_id: str | None = None) -> list[dict]:
        return self._rows("alerts", limit, series_id)

    def list_audit(self, limit: int = 50, series_id: str | None = None) -> list[dict]:
        rows = self._rows("audit_log", limit, series_id)
        for r in rows:
            r["dry_run"] = bool(r["dry_run"])
            r["details"] = json.loads(r["details"] or "{}")
        return rows

    def count_alerts(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"])

    def last_action_time(self, target: str) -> datetime | None:
        ph = ",".join("?" * len(COUNTED_OUTCOMES))
        with self._lock:
            row = self._db.execute(
                f"SELECT MAX(ts) AS t FROM audit_log WHERE target=? AND outcome IN ({ph})",
                (target, *COUNTED_OUTCOMES)).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    def count_actions_since(self, since: datetime, until: datetime) -> int:
        ph = ",".join("?" * len(COUNTED_OUTCOMES))
        with self._lock:
            row = self._db.execute(
                f"SELECT COUNT(*) AS n FROM audit_log WHERE ts>? AND ts<=? AND outcome IN ({ph})",
                (_iso(since), _iso(until), *COUNTED_OUTCOMES)).fetchone()
        return int(row["n"])

    @staticmethod
    def hour_before(ts: datetime) -> datetime:
        return ts - timedelta(hours=1)
