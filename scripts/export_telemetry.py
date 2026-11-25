from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from app.core.config import get_settings
from app.core.domain import METRIC_COLUMNS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export persisted PredictiveOps telemetry to CSV."
    )
    parser.add_argument(
        "--scope",
        required=True,
        choices=("host", "application", "combined"),
    )
    parser.add_argument("--series-id")
    parser.add_argument("--since", help="Inclusive ISO timestamp")
    parser.add_argument("--until", help="Exclusive ISO timestamp")
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: data/exports/<scope>-telemetry.csv)",
    )
    args = parser.parse_args()

    settings = get_settings()
    database = Path(settings.sqlite_path)
    if not database.exists():
        raise SystemExit(f"Telemetry database does not exist: {database}")

    output = Path(args.output) if args.output else Path(
        f"data/exports/{args.scope}-telemetry.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    columns = [
        "timestamp",
        "scope",
        "series_id",
        *METRIC_COLUMNS,
        "ingestion_source",
    ]

    conditions = ["scope = ?"]
    parameters: list[str] = [args.scope]

    if args.series_id:
        conditions.append("series_id = ?")
        parameters.append(args.series_id)
    if args.since:
        conditions.append("timestamp >= ?")
        parameters.append(args.since)
    if args.until:
        conditions.append("timestamp < ?")
        parameters.append(args.until)

    query = (
        f"SELECT {', '.join(columns)} FROM telemetry_samples "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY series_id, timestamp"
    )

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(query, parameters)
        rows = cursor.fetchall()

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)

    print(f"Database: {database}")
    print(f"Scope: {args.scope}")
    print(f"Rows exported: {len(rows)}")
    print(f"Output: {output.resolve()}")
    print("Labels: none — this export contains raw observations only.")


if __name__ == "__main__":
    main()
