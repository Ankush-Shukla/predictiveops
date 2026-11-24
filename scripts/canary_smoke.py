#!/usr/bin/env python
"""Predictive deployment-validation smoke test for the CHAMPION model (used by CI).

Runs the canary simulation over many seeds: healthy candidates must (almost) never be rolled back and
faulty candidates must (almost always) be rolled back. Exit code 3 if either rate misses its limit.
All data is SYNTHETIC; this guards against regressions in the model/canary logic, not real-world behaviour.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import create_app  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.models.predictor import Predictor  # noqa: E402

FAULTS = ("memory_leak", "latency_creep", "error_ramp", "cpu_contention", "disk_saturation")


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-root", type=Path, default=s.model_dir)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--max-false-rollback", type=float, default=0.05)
    ap.add_argument("--min-detection", type=float, default=0.90)
    a = ap.parse_args(argv)

    predictor = Predictor.from_champion(a.model_root)
    tmp = Path(tempfile.mkdtemp()) / "smoke.sqlite"
    settings = Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp)
    app = create_app(settings, predictor, load_model=False)
    results: dict[str, dict] = {}
    with TestClient(app) as c:
        for fault in ("none", *FAULTS):
            decisions = [c.post("/v1/canary/simulate", json={"candidate_fault": fault, "seed": seed}).json()
                         ["final"]["decision"] for seed in range(a.seeds)]
            results[fault] = {d: decisions.count(d) for d in sorted(set(decisions))}
    healthy_rb = results["none"].get("rollback", 0) / a.seeds
    detect = sum(results[f].get("rollback", 0) for f in FAULTS) / (a.seeds * len(FAULTS))
    ok = healthy_rb <= a.max_false_rollback and detect >= a.min_detection
    print(json.dumps({"model_version": predictor.version, "seeds": a.seeds, "results": results,
                      "false_rollback_rate": healthy_rb, "detection_rate": detect, "passed": ok}, indent=2))
    print("NOTE: SYNTHETIC data; in-distribution simulation, not evidence of real-world performance.")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
