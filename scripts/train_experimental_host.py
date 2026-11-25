#!/usr/bin/env python
"""Train an isolated synthetic host model for experimental live telemetry scoring.

This model is not production-qualified and is never promoted to production champions.
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
    expected_root = (settings.model_dir / "experimental-host").resolve()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--series", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--model-type", choices=("hgb", "logreg", "xgb"), default="hgb")
    parser.add_argument("--model-root", type=Path, default=expected_root)
    parser.add_argument("--min-f1", type=float, default=0.70)
    parser.add_argument("--max-fpr", type=float, default=0.05)
    args = parser.parse_args(argv)

    if args.days < 1 or args.series < 1 or args.horizon < 1:
        parser.error("--days, --series, and --horizon must be positive")

    if not 0.0 <= args.min_f1 <= 1.0:
        parser.error("--min-f1 must be between 0 and 1")

    if not 0.0 <= args.max_fpr <= 1.0:
        parser.error("--max-fpr must be between 0 and 1")

    model_root = args.model_root.resolve()
    if model_root != expected_root:
        parser.error(
            f"--model-root must be the isolated experimental directory: {expected_root}"
        )

    config = GeneratorConfig(
        days=args.days,
        n_series=args.series,
        seed=args.seed,
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
        [
            add_labels(group, args.horizon)
            for _, group in frame.groupby("series_id", sort=False)
        ],
        ignore_index=True,
    )

    result = run_training(
        frame,
        TrainConfig(
            model_type=args.model_type,
            scope="host",
            horizon_minutes=args.horizon,
            seed=args.seed,
            gates=GateConfig(min_f1=args.min_f1, max_fpr=args.max_fpr),
            model_root=model_root,
            use_mlflow=False,
            promote=False,
        ),
    )

    report = result.report
    gates_passed = report["gates"]["passed"] is True

    if gates_passed:
        registry.set_champion(model_root, result.version)

    print(json.dumps({
        "version": result.version,
        "installed_as_experimental_champion": gates_passed,
        "model_root": str(model_root),
        "scope": "host",
        "data_source": result.bundle.metadata["data_source"],
        "training_rows": len(frame),
        "test": {
            key: value
            for key, value in report["test"].items()
            if key != "calibration_curve"
        },
        "event": report["event"],
        "gates": report["gates"],
    }, indent=2, default=str))

    if not gates_passed:
        print(
            "QUALITY GATES FAILED: model artifact retained for inspection, "
            "but experimental champion was not changed."
        )
        return 2

    print(
        "WARNING: Synthetic-trained experimental model. "
        "Never use as production failure evidence."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
