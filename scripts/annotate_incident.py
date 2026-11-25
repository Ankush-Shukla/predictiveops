#!/usr/bin/env python3
"""Record and inspect verified incidents for PredictiveOps training data."""

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB_DEFAULT = "/srv/artifacts/predictiveops.sqlite"


def connect(path):
    db = sqlite3.connect(path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            category TEXT NOT NULL,
            evidence TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed'
                CHECK(status IN ('confirmed', 'retracted')),
            created_at TEXT NOT NULL,
            UNIQUE(series_id, started_at, ended_at, category)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS ix_incidents_series_time
        ON incidents(series_id, started_at, ended_at)
    """)
    db.commit()
    return db


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone, e.g. +00:00")
    return parsed.astimezone(UTC).isoformat()


def add(args):
    start = timestamp(args.start)
    end = timestamp(args.end)

    if end <= start:
        raise SystemExit("Incident end must be later than its start.")

    with connect(args.db) as db:
        db.execute("""
            INSERT INTO incidents (
                series_id, started_at, ended_at, category,
                evidence, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            args.series_id,
            start,
            end,
            args.category,
            args.evidence,
            args.source,
            datetime.now(UTC).isoformat(),
        ))

    print("Confirmed incident recorded.")


def listing(args):
    with connect(args.db) as db:
        rows = db.execute("""
            SELECT id, series_id, started_at, ended_at,
                   category, evidence, source, status
            FROM incidents
            WHERE (? IS NULL OR series_id = ?)
            ORDER BY started_at
        """, (args.series_id, args.series_id)).fetchall()

    if not rows:
        print("No incident annotations found.")
        return

    for row in rows:
        print(
            f"\nID: {row[0]}\nSeries: {row[1]}\n"
            f"Start: {row[2]}\nEnd: {row[3]}\n"
            f"Category: {row[4]}\nEvidence: {row[5]}\n"
            f"Source: {row[6]}\nStatus: {row[7]}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_DEFAULT)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("add")
    create.add_argument("--series-id", required=True)
    create.add_argument("--start", required=True)
    create.add_argument("--end", required=True)
    create.add_argument("--category", required=True)
    create.add_argument("--evidence", required=True)
    create.add_argument("--source", required=True)
    create.set_defaults(func=add)

    show = commands.add_parser("list")
    show.add_argument("--series-id")
    show.set_defaults(func=listing)

    args = parser.parse_args()
    if not Path(args.db).exists():
        raise SystemExit(f"Database not found: {args.db}")
    args.func(args)


if __name__ == "__main__":
    main()
