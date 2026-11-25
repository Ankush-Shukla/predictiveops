#!/usr/bin/env python
"""Train an isolated synthetic host model for experimental scoring of live host telemetry.

This model is intentionally NOT production-qualified and is never promoted to the
production host/application champion directories.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.models import registry
from data.synthetic.generator import GeneratorConfig, add_labels, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--series", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--model-type", choices=("hgb", "logreg", "xgb"), default="hgb")
    parser.add_argument("--model-root", type=Path, default=settings.model_dir / "experimental-host")
    parser.add_argument("--min-f1", type=float, default=0.70)
    parser.add_argument("--max-fpr", type=float, default=0.05)
    args = parser.parse_args(argv)

    # Host-only fault mechanisms and labels prevent application latency/error signals
    # from leaking into a model that receives only host resource metrics.
    config = GeneratorConfig(
        days=args.days, n_series=args.series, seed=args.seed,
        horizon_minutes=args.horizon,
        fault_types=("memory_leak", "cpu_contention", "disk_saturation"),
    )
    frame = generate_dataset(config)
    frame["is_unhealthy"] = (
        (frame["cpu_pct"] >= 98.0)
        | (frame["memory_pct"] >= 95.0)
        | (frame["disk_io_util_pct"] >= 98.0)
    )
    frame = pd.concat(
        [add_labels(group, args.horizon) for _, group in frame.groupby("series_id", sort=False)],
        ignore_index=True,
    )

    result = run_training(frame, TrainConfig(
        model_type=args.model_type, scope="host", horizon_minutes=args.horizon,
        seed=args.seed, gates=GateConfig(min_f1=args.min_f1, max_fpr=args.max_fpr),
        model_root=args.model_root, use_mlflow=False, promote=False,
    ))
    # Install only into the isolated experimental directory. Quality-gate results remain
    # visible but do not block a side-effect-free demo; production champions are untouched.
    registry.set_champion(args.model_root, result.version)
    report = result.report
    print(json.dumps({
        "version": result.version,
        "installed_as_experimental_champion": True,
        "model_root": str(args.model_root),
        "scope": "host",
        "data_source": result.bundle.metadata["data_source"],
        "training_rows": len(frame),
        "test": {k: v for k, v in report["test"].items() if k != "calibration_curve"},
        "event": report["event"],
        "gates": report["gates"],
    }, indent=2, default=str))
    print("WARNING: synthetic-trained experimental model. Do not use as production failure evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
