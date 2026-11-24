#!/usr/bin/env python
"""Write a Markdown summary of the champion model's evaluation (for the GitHub Actions job summary)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/models")
    champ = json.loads((root / "champion.json").read_text())["version"]
    meta = json.loads((root / champ / "metadata.json").read_text())
    t, e, g = meta["evaluation"]["test"], meta["evaluation"]["event"], meta["evaluation"]["gates"]
    print(f"### Model `{champ}` ({meta['model_type']}, data: **{meta['data_source']}**)\n")
    print("| metric | value |\n|---|---|")
    for k in ("f1", "precision", "recall", "fpr", "pr_auc", "brier", "ece"):
        print(f"| {k} | {t[k]:.4f} |")
    print(f"| event recall | {e['event_recall']:.3f} ({e['detected_events']}/{e['failure_events']}) |")
    print("\n| gate | passed | value | limit |\n|---|---|---|---|")
    for c in g["checks"]:
        print(f"| {c['name']} | {'yes' if c['passed'] else 'NO'} | {c['value']:.4f} | {c['limit']:.4f} |")
    print(f"\n> {meta['disclaimer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
