"""Durable storage for observed telemetry. No labels or predictions are inferred here."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.core.domain import METRIC_COLUMNS


class TelemetryStore:
    """Persistent, deduplicated telemetry history stored in SQLite."""

    def __init__(self, path: str | Path) -> None:
        p = str(path)
        if p != ":memory:":
            Path(p).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._db = sqlite3.connect(p, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row

        with self._lock:
            if p != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=30000")
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL CHECK(scope IN ('host', 'application', 'combined')),
                    series_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    cpu_pct REAL NOT NULL,
                    memory_pct REAL NOT NULL,
                    disk_io_util_pct REAL NOT NULL,
                    network_rx_mbps REAL NOT NULL,
                    network_tx_mbps REAL NOT NULL,
                    request_rate_rps REAL NOT NULL,
                    latency_p95_ms REAL NOT NULL,
                    error_rate REAL NOT NULL,
                    ingestion_source TEXT NOT NULL,
                    UNIQUE(scope, series_id, timestamp)
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS ix_telemetry_scope_ts
                ON telemetry_samples(scope, timestamp)
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS ix_telemetry_series_ts
                ON telemetry_samples(series_id, timestamp)
            """)
            self._db.commit()

    def add_samples(
        self,
        scope: str,
        series_id: str,
        rows: list[dict],
        ingestion_source: str,
    ) -> int:
        if scope not in {"host", "application", "combined"}:
            raise ValueError(f"invalid telemetry scope: {scope}")
        if not rows:
            return 0

        columns = (
            "scope", "series_id", "timestamp", *METRIC_COLUMNS, "ingestion_source"
        )
        placeholders = ",".join("?" for _ in columns)
        values = []

        for row in rows:
            timestamp = row["timestamp"]
            if hasattr(timestamp, "isoformat"):
                timestamp = timestamp.isoformat()
            values.append((
                scope,
                series_id,
                str(timestamp),
                *(float(row[name]) for name in METRIC_COLUMNS),
                ingestion_source,
            ))

        with self._lock, self._db:
            before = self._db.total_changes
            self._db.executemany(
                f"""
                INSERT OR IGNORE INTO telemetry_samples ({",".join(columns)})
                VALUES ({placeholders})
                """,
                values,
            )
            return self._db.total_changes - before

    def count(self, scope: str | None = None) -> int:
        with self._lock:
            if scope is None:
                row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM telemetry_samples"
                ).fetchone()
            else:
                row = self._db.execute(
                    "SELECT COUNT(*) AS n FROM telemetry_samples WHERE scope=?",
                    (scope,),
                ).fetchone()
            return int(row["n"])

    def close(self) -> None:
        with self._lock:
            self._db.close()
