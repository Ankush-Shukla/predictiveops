#!/usr/bin/env python
"""Generate the synthetic PredictiveOps dataset.

Example:
    python scripts/generate_data.py --days 14 --series 4 --seed 42 --out data/generated/synthetic.csv.gz
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from data.synthetic.generator import (  # noqa: E402
    GeneratorConfig,
    dataset_summary,
    generate_dataset,
    write_dataset,
)


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--series", type=int, default=4)
    ap.add_argument("--seed", type=int, default=s.synthetic_seed)
    ap.add_argument("--horizon", type=int, default=s.horizon_minutes, help="minutes (15-30)")
    ap.add_argument("--out", type=Path, default=s.data_dir / "synthetic.csv.gz")
    args = ap.parse_args(argv)

    configure_logging(s.log_level, s.log_json)
    log = get_logger("generate_data")
    cfg = GeneratorConfig(days=args.days, n_series=args.series, seed=args.seed,
                          horizon_minutes=args.horizon)
    df = generate_dataset(cfg)
    path = write_dataset(df, args.out, cfg)
    summary = dataset_summary(df)
    log.info("dataset written", extra={"path": str(path), **summary})
    print(json.dumps({"path": str(path), **summary}, indent=2))
    print("NOTE: this is SYNTHETIC data, not real production measurements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
