#!/usr/bin/env python
"""Train, evaluate, gate and (optionally) promote a failure-prediction model.

Example:
    python scripts/train.py --data data/generated/synthetic.csv.gz --model-type hgb --promote
Exit code 2 with --enforce-gates when the quality gates fail (used by CI).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.domain import SCOPE_METRIC_COLUMNS
from app.core.logging import configure_logging  # noqa: E402
from data.synthetic.generator import load_dataset  # noqa: E402
from training.gates import GateConfig  # noqa: E402
from training.train import MODEL_TYPES, TrainConfig, run_training  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", type=Path, default=s.data_dir / "synthetic.csv.gz")
    ap.add_argument("--model-type", choices=MODEL_TYPES, default="hgb")
    ap.add_argument("--scope", choices=tuple(SCOPE_METRIC_COLUMNS), default="combined")
    ap.add_argument("--horizon", type=int, default=s.horizon_minutes)
    ap.add_argument("--seed", type=int, default=s.synthetic_seed)
    ap.add_argument("--model-root", type=Path, default=s.model_dir)
    ap.add_argument("--min-f1", type=float, default=s.gate_min_f1)
    ap.add_argument("--max-fpr", type=float, default=s.gate_max_fpr)
    ap.add_argument("--no-mlflow", action="store_true")
    ap.add_argument("--promote", action="store_true", help="set champion if gates pass")
    ap.add_argument("--enforce-gates", action="store_true", help="exit 2 if gates fail")
    ap.add_argument("--version", default=None)
    a = ap.parse_args(argv)

    configure_logging(s.log_level, s.log_json)
    cfg = TrainConfig(
        model_type=a.model_type, scope=a.scope, horizon_minutes=a.horizon, seed=a.seed,
        max_fpr_for_threshold=a.max_fpr, gates=GateConfig(min_f1=a.min_f1, max_fpr=a.max_fpr),
        model_root=a.model_root, use_mlflow=not a.no_mlflow, tracking_uri=s.mlflow_tracking_uri,
        experiment=s.mlflow_experiment, registered_model_name=s.mlflow_model_name,
        promote=a.promote, version=a.version)
    res = run_training(load_dataset(a.data), cfg)

    r = res.report
    slim = lambda m: {k: round(v, 4) if isinstance(v, float) else v  # noqa: E731
                      for k, v in m.items() if k != "calibration_curve"}
    print(json.dumps({
        "version": res.version, "promoted": res.promoted, "gates": r["gates"],
        "test_ml": slim(r["test"]), "test_baseline_default": slim(r["baseline_default"]),
        "test_baseline_tuned": slim(r["baseline_tuned"]), "event": slim(r["event"]),
        "calibration": r["calibration"], "mlflow": res.mlflow}, indent=2, default=str))
    print(f"NOTE: evaluation data source: {res.bundle.metadata.get('data_source', 'unknown').upper()}.")
    return 2 if (a.enforce_gates and not r["gates"]["passed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
