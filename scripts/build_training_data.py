#!/usr/bin/env python
"""Build a real-data training file from stored telemetry and confirmed incident annotations.

Joins ``telemetry_samples`` (collected by the API poller) with the ``incidents`` table written by
``scripts/annotate_incident.py``. A minute is ``is_unhealthy`` only when it falls inside a confirmed,
non-retracted incident for the same series. Nothing is inferred or simulated: no incidents means no
positive labels, and the script refuses to write a file that training could not use.

Example (inside the stack):
    docker compose --profile training run --rm build-training-data --scope host
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.domain import SCOPE_METRIC_COLUMNS  # noqa: E402

DATA_SOURCE = "production"


def load_telemetry(db: sqlite3.Connection, scope: str, series_id: str | None) -> pd.DataFrame:
    metrics = SCOPE_METRIC_COLUMNS[scope]
    query = f"SELECT series_id, timestamp, {', '.join(metrics)} FROM telemetry_samples WHERE scope = ?"
    params: list[str] = [scope]
    if series_id:
        query += " AND series_id = ?"
        params.append(series_id)
    df = pd.read_sql_query(query + " ORDER BY series_id, timestamp", db, params=params)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    return df


def load_incidents(db: sqlite3.Connection) -> pd.DataFrame:
    exists = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'").fetchone()
    if not exists:
        return pd.DataFrame(columns=["series_id", "started_at", "ended_at"])
    df = pd.read_sql_query(
        "SELECT series_id, started_at, ended_at FROM incidents WHERE status = 'confirmed'", db)
    for col in ("started_at", "ended_at"):
        df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    return df


def label_unhealthy(telemetry: pd.DataFrame, incidents: pd.DataFrame) -> pd.Series:
    """True for samples inside a confirmed incident window of the same series (inclusive start)."""
    labels = pd.Series(False, index=telemetry.index)
    for incident in incidents.itertuples():
        inside = ((telemetry["series_id"] == incident.series_id)
                  & (telemetry["timestamp"] >= incident.started_at)
                  & (telemetry["timestamp"] < incident.ended_at))
        labels |= inside
    return labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scope", required=True, choices=("host", "application"))
    ap.add_argument("--db", type=Path, default=Path("/srv/artifacts/predictiveops.sqlite"))
    ap.add_argument("--series-id", help="restrict to one series (default: all in the scope)")
    ap.add_argument("--out", type=Path, default=None,
                    help="default: /srv/training-data/<scope>.csv.gz")
    ap.add_argument("--min-incidents", type=int, default=3,
                    help="refuse to write fewer distinct incident windows than this (default 3)")
    args = ap.parse_args(argv)

    if not args.db.exists():
        raise SystemExit(f"Telemetry database not found: {args.db}")
    out = args.out or Path(f"/srv/training-data/{args.scope}.csv.gz")

    with sqlite3.connect(args.db) as db:
        telemetry = load_telemetry(db, args.scope, args.series_id)
        incidents = load_incidents(db)
    if args.series_id:
        incidents = incidents[incidents["series_id"] == args.series_id]
    incidents = incidents[incidents["series_id"].isin(telemetry["series_id"].unique())]

    if telemetry.empty:
        raise SystemExit(f"No {args.scope} telemetry stored yet. Let the API poll for a while first.")
    telemetry["is_unhealthy"] = label_unhealthy(telemetry, incidents)
    telemetry["data_source"] = DATA_SOURCE
    telemetry["timestamp"] = telemetry["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)

    positives = int(telemetry["is_unhealthy"].sum())
    print(f"Scope: {args.scope}")
    print(f"Samples: {len(telemetry)} across {telemetry['series_id'].nunique()} series "
          f"({telemetry['timestamp'].min()} to {telemetry['timestamp'].max()} UTC)")
    print(f"Confirmed incident windows: {len(incidents)}  |  unhealthy minutes: {positives}")

    if len(incidents) < args.min_incidents or positives == 0:
        raise SystemExit(
            f"Refusing to write {out}: need at least {args.min_incidents} confirmed incident windows "
            "covered by stored telemetry. Record real incidents with scripts/annotate_incident.py. "
            "Training also needs unhealthy minutes in the train, validation and test splits.")

    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["timestamp", "series_id", *SCOPE_METRIC_COLUMNS[args.scope], "is_unhealthy", "data_source"]
    telemetry[cols].to_csv(out, index=False)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
