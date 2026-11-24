"""Robustness studies beyond the standard test split (all on SYNTHETIC data).

* ``seed_variability``  - retrain on independently simulated datasets: how stable are the metrics?
* ``holdout_fault``     - train WITHOUT one fault mechanism, test on data containing only that mechanism.
* ``canary_study``      - canary decisions over many seeds, including faults that start late.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from app.models.bundle import ModelBundle
from app.models.predictor import Predictor
from app.services.canary_models import CanaryConfig
from app.services.canary_sim import simulate_canary
from data.synthetic.generator import FAULT_TYPES, GeneratorConfig, generate_dataset
from training.baseline import threshold_score
from training.evaluate import confusion_metrics, event_records, summarize_events
from training.gates import GateConfig
from training.train import TrainConfig, prepare, run_training


def _quick_cfg(model_type: str, root: Path, seed: int = 42) -> TrainConfig:
    return TrainConfig(model_type=model_type, model_root=root, use_mlflow=False, seed=seed,
                       gates=GateConfig(min_f1=0.0, max_fpr=1.0, must_beat_baseline_pr_auc=False))


def evaluate_bundle(df: pd.DataFrame, bundle: ModelBundle, horizon: int = 20) -> dict:
    """Score an ENTIRE dataset with a trained bundle (threshold fixed by training, not re-tuned)."""
    df, feats, _ = prepare(df, horizon)
    fb = bundle.feature_builder()  # verifies schema parity
    ok = df["label_valid"] & ~df["is_unhealthy"] & feats.notna().all(axis=1)
    x = feats.loc[ok, fb.feature_names]
    p = bundle.calibrator.predict(bundle.estimator.predict_proba(x)[:, 1])
    y = df.loc[ok, "label_failure_within_h"].to_numpy()
    m = confusion_metrics(y, p >= bundle.threshold)
    pred = pd.Series(p >= bundle.threshold, index=x.index)
    cols = [c for c in ("series_id", "timestamp", "is_unhealthy", "fault_type", "episode_id") if c in df]
    recs = event_records(df[cols], pred, horizon)
    ev = summarize_events(recs)
    base_pred = pd.Series(threshold_score(x) >= 1.0, index=x.index)  # static warning thresholds
    bev = summarize_events(event_records(df[cols], base_pred, horizon))
    bm = confusion_metrics(y, base_pred.to_numpy())
    return {"rows": int(len(y)), "positives": int(y.sum()), "precision": m["precision"],
            "recall": m["recall"], "f1": m["f1"], "fpr": m["fpr"], **ev,
            "baseline": {"recall": bm["recall"], "fpr": bm["fpr"], "event_recall": bev["event_recall"],
                         "median_lead_time_min": bev["median_lead_time_min"]}}


def seed_variability(seeds: list[int], *, model_type: str = "hgb", days: int = 14, n_series: int = 4) -> dict:
    rows = []
    with tempfile.TemporaryDirectory() as td:
        for s in seeds:
            df = generate_dataset(GeneratorConfig(days=days, n_series=n_series, seed=s))
            r = run_training(df, _quick_cfg(model_type, Path(td) / f"s{s}")).report
            rows.append({"seed": s, "f1": r["test"]["f1"], "fpr": r["test"]["fpr"],
                         "pr_auc": r["test"]["pr_auc"], "event_recall": r["event"]["event_recall"],
                         "baseline_f1": r["baseline_default"]["f1"],
                         "baseline_pr_auc": r["baseline_default"]["pr_auc"],
                         "failure_events": r["event"]["failure_events"]})
    keys = ("f1", "fpr", "pr_auc", "event_recall", "baseline_f1", "baseline_pr_auc")
    summary = {k: {"mean": float(np.mean([r[k] for r in rows])), "std": float(np.std([r[k] for r in rows])),
                   "min": float(np.min([r[k] for r in rows])), "max": float(np.max([r[k] for r in rows]))}
               for k in keys}
    return {"runs": rows, "summary": summary}


def holdout_fault(fault: str, *, model_type: str = "hgb", days: int = 14, n_series: int = 4,
                  test_days: int = 10) -> dict:
    """Train without ``fault``; compare on unseen-mechanism data vs. fresh data of seen mechanisms."""
    others = tuple(f for f in FAULT_TYPES if f != fault)
    train_df = generate_dataset(GeneratorConfig(days=days, n_series=n_series, seed=42, fault_types=others))
    with tempfile.TemporaryDirectory() as td:
        res = run_training(train_df, _quick_cfg(model_type, Path(td)))
    unseen = generate_dataset(GeneratorConfig(days=test_days, n_series=n_series, seed=999,
                                              fault_types=(fault,)))
    seen = generate_dataset(GeneratorConfig(days=test_days, n_series=n_series, seed=998, fault_types=others))
    return {"held_out": fault, "trained_on": list(others),
            "unseen": evaluate_bundle(unseen, res.bundle), "seen": evaluate_bundle(seen, res.bundle)}


def canary_study(predictor: Predictor, cfg: CanaryConfig, *, healthy_seeds: int = 150,
                 seeds_per_cell: int = 20, fault_starts: tuple[int, ...] = (10, 30, 60, 90, 120, 150),
                 minutes: int = 300) -> dict:
    healthy = Counter(simulate_canary(predictor, cfg, "none", seed=s, minutes=minutes).final.decision
                      for s in range(healthy_seeds))
    grid: dict[str, dict] = {}
    for fault in FAULT_TYPES:
        grid[fault] = {}
        for fs in fault_starts:
            runs = [simulate_canary(predictor, cfg, fault, seed=s, minutes=minutes, fault_start=fs)
                    for s in range(seeds_per_cell)]
            dec = Counter(r.final.decision for r in runs)
            mins = [r.final_minute for r in runs if r.final.decision == "rollback"]
            grid[fault][str(fs)] = {"decisions": dict(dec),
                                    "median_rollback_minute": float(np.median(mins)) if mins else None}
    return {"minutes": minutes, "min_observation_minutes": cfg.min_observation_minutes,
            "healthy_seeds": healthy_seeds, "healthy_decisions": dict(healthy),
            "seeds_per_cell": seeds_per_cell, "grid": grid}
