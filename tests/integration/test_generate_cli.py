import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cli_end_to_end(tmp_path):
    out = tmp_path / "d.csv.gz"
    r = subprocess.run(
        [sys.executable, "scripts/generate_data.py", "--days", "3", "--series", "2",
         "--seed", "1", "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    summary = json.loads(r.stdout.split("\nNOTE")[0])
    assert summary["rows"] == 3 * 1440 * 2 and summary["data_source"] == "synthetic"
    assert out.exists() and (tmp_path / "d.meta.json").exists()
    assert "SYNTHETIC" in r.stdout


def test_cli_rejects_bad_horizon(tmp_path):
    r = subprocess.run(
        [sys.executable, "scripts/generate_data.py", "--horizon", "99", "--out", str(tmp_path / "x.csv.gz")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode != 0
