import sqlite3

import pytest

from scripts import build_training_data as btd


def _db_with(samples, incidents):
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE telemetry_samples (scope TEXT, series_id TEXT, timestamp TEXT,
        cpu_pct REAL, memory_pct REAL, disk_io_util_pct REAL, network_rx_mbps REAL, network_tx_mbps REAL,
        request_rate_rps REAL, latency_p95_ms REAL, error_rate REAL, ingestion_source TEXT)""")
    db.execute("""CREATE TABLE incidents (series_id TEXT, started_at TEXT, ended_at TEXT, status TEXT)""")
    db.executemany("INSERT INTO telemetry_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", samples)
    db.executemany("INSERT INTO incidents VALUES (?,?,?,?)", incidents)
    return db


def _row(series, minute):
    return ("host", series, f"2026-01-01T00:{minute:02d}:00+00:00", 1, 2, 3, 4, 5, 0, 0, 0, "t")


def test_only_confirmed_incident_windows_are_labelled():
    samples = [_row("h", m) for m in range(10)] + [_row("other", m) for m in range(10)]
    incidents = [("h", "2026-01-01T00:03:00+00:00", "2026-01-01T00:05:00+00:00", "confirmed"),
                 ("h", "2026-01-01T00:08:00+00:00", "2026-01-01T00:09:00+00:00", "retracted")]
    db = _db_with(samples, incidents)
    telemetry = btd.load_telemetry(db, "host", None)
    labels = btd.label_unhealthy(telemetry, btd.load_incidents(db))
    flagged = telemetry[labels]
    assert list(flagged["series_id"].unique()) == ["h"]
    assert [t.minute for t in flagged["timestamp"]] == [3, 4]


def test_no_incidents_means_no_positive_labels():
    db = _db_with([_row("h", m) for m in range(5)], [])
    telemetry = btd.load_telemetry(db, "host", None)
    assert not btd.label_unhealthy(telemetry, btd.load_incidents(db)).any()


def test_refuses_to_write_without_enough_incidents(tmp_path):
    path = tmp_path / "p.sqlite"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE telemetry_samples (scope TEXT, series_id TEXT, timestamp TEXT,
        cpu_pct REAL, memory_pct REAL, disk_io_util_pct REAL, network_rx_mbps REAL, network_tx_mbps REAL,
        request_rate_rps REAL, latency_p95_ms REAL, error_rate REAL, ingestion_source TEXT)""")
    db.execute("INSERT INTO telemetry_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", _row("h", 0))
    db.commit(); db.close()
    out = tmp_path / "host.csv.gz"
    with pytest.raises(SystemExit):
        btd.main(["--scope", "host", "--db", str(path), "--out", str(out)])
    assert not out.exists()
