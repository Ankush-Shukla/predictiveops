#!/usr/bin/env python
"""Robustness studies on SYNTHETIC data: seed variability, held-out fault mechanisms, canary study.

    python scripts/evaluate_robustness.py --parts seeds,holdout,canary \\
        --json artifacts/reports/robustness.json --markdown artifacts/reports/robustness.md
The canary part needs a promoted champion (python scripts/train.py --promote).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.models.predictor import Predictor  # noqa: E402
from data.synthetic.generator import FAULT_TYPES  # noqa: E402
from training.robustness import canary_study, holdout_fault, seed_variability  # noqa: E402


def render_markdown(r: dict) -> str:
    out = ["# Robustness studies (SYNTHETIC data)\n",
           "> Everything below comes from the project's own simulator. It measures sensitivity of the",
           "> pipeline to data draws and mechanisms, not real-world performance.\n"]
    if "seeds" in r:
        s = r["seeds"]["summary"]
        out += [f"## Seed variability ({len(r['seeds']['runs'])} independently simulated datasets)\n",
                "| metric | mean | std | min | max |", "|---|---|---|---|---|"]
        for k, v in s.items():
            out.append(f"| {k} | {v['mean']:.3f} | {v['std']:.3f} | {v['min']:.3f} | {v['max']:.3f} |")
    if "holdout" in r:
        out += ["\n## Held-out fault mechanism\n",
                "Train without one mechanism, test on data containing only it (unseen) vs. fresh data of the "
                "mechanisms it was trained on (seen).\n",
                "| held out | model detected (unseen) | model lead | model FPR | static detected "
                "| static lead | static FPR | model detected (seen mechanisms) |",
                "|---|---|---|---|---|---|---|---|"]
        for h in r["holdout"]:
            u, s, b = h["unseen"], h["seen"], h["unseen"]["baseline"]
            out.append(f"| {h['held_out']} | {u['detected_events']}/{u['failure_events']} "
                       f"| {u['median_lead_time_min']:.0f} min | {u['fpr']:.4f} "
                       f"| {round(b['event_recall'] * u['failure_events'])}/{u['failure_events']} "
                       f"| {b['median_lead_time_min']:.0f} min | {b['fpr']:.4f} | {s['event_recall']:.3f} |")
    for c in r.get("canary", []):
        cols = list(next(iter(c["grid"].values())))
        out += [f"\n## Canary study: minimum observation {c['min_observation_minutes']} min "
                f"({c['minutes']}-min runs)\n",
                f"Healthy candidates over {c['healthy_seeds']} seeds: {c['healthy_decisions']}\n",
                f"Faulty candidates, {c['seeds_per_cell']} seeds per cell; value = rollbacks / runs "
                "(a fault that starts after the decision is promoted):\n",
                "| fault | " + " | ".join(f"starts at min {k}" for k in cols) + " |",
                "|---|" + "---|" * len(cols)]
        for fault, row in c["grid"].items():
            cells = [f"{v['decisions'].get('rollback', 0)}/{sum(v['decisions'].values())}"
                     for v in row.values()]
            out.append(f"| {fault} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--parts", default="seeds,holdout,canary")
    ap.add_argument("--model-type", default="hgb")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--canary-min-obs", default="20,60,120", help="comma-separated min observation minutes")
    ap.add_argument("--model-root", type=Path, default=s.model_dir)
    ap.add_argument("--json", type=Path, default=Path("artifacts/reports/robustness.json"))
    ap.add_argument("--markdown", type=Path, default=None)
    a = ap.parse_args(argv)
    parts = set(a.parts.split(","))
    r: dict = {"data_source": "synthetic"}
    if "seeds" in parts:
        r["seeds"] = seed_variability(list(range(1, a.seeds + 1)), model_type=a.model_type, days=a.days)
    if "holdout" in parts:
        r["holdout"] = [holdout_fault(f, model_type=a.model_type, days=a.days) for f in FAULT_TYPES]
    if "canary" in parts:
        pred = Predictor.from_champion(a.model_root)
        r["canary"] = []
        for m in (int(x) for x in a.canary_min_obs.split(",")):
            top = max(m, s.canary.max_observation_minutes)
            cfg = s.canary.model_copy(update={"min_observation_minutes": m, "max_observation_minutes": top})
            r["canary"].append(canary_study(pred, cfg, minutes=max(300, m + 200)))
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(r, indent=2, default=str))
    md = render_markdown(r)
    if a.markdown:
        a.markdown.write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
