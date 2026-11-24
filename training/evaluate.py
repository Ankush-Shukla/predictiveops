"""Evaluation: row-level metrics, threshold selection, calibration curve, event-level metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def select_threshold(scores: np.ndarray, y: np.ndarray, max_fpr: float) -> tuple[float, bool]:
    """Pick the threshold maximising F1 subject to FPR <= max_fpr (on VALIDATION data).

    Returns (threshold, constraint_met). If no threshold meets the FPR cap, returns the
    best-F1 threshold with constraint_met=False.
    """
    grid = np.unique(np.quantile(scores, np.linspace(0.5, 0.9995, 400)))
    best, best_f1, best_any, best_any_f1 = None, -1.0, grid[-1], -1.0
    for t in grid:
        m = confusion_metrics(y, scores >= t)
        if m["f1"] > best_any_f1:
            best_any, best_any_f1 = float(t), m["f1"]
        if m["fpr"] <= max_fpr and m["f1"] > best_f1:
            best, best_f1 = float(t), m["f1"]
    return (best, True) if best is not None else (best_any, False)


def confusion_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    y = np.asarray(y).astype(bool)
    pred = np.asarray(pred).astype(bool)
    tp = int((y & pred).sum())
    fp = int((~y & pred).sum())
    fn = int((y & ~pred).sum())
    tn = int((~y & ~pred).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec,
            "f1": f1, "fpr": fp / (fp + tn) if fp + tn else 0.0}


def calibration_curve(y: np.ndarray, p: np.ndarray, bins: int = 10) -> tuple[list[dict], float]:
    """Equal-count bins; returns (curve, expected calibration error)."""
    order = np.argsort(p)
    curve, ece = [], 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk) == 0:
            continue
        mp, fo = float(p[chunk].mean()), float(np.asarray(y)[chunk].mean())
        curve.append({"mean_predicted": mp, "observed_rate": fo, "n": int(len(chunk))})
        ece += len(chunk) / len(p) * abs(mp - fo)
    return curve, float(ece)


def score_metrics(y: np.ndarray, scores: np.ndarray, threshold: float, *, probabilistic: bool) -> dict:
    m = confusion_metrics(y, scores >= threshold)
    m["threshold"] = float(threshold)
    m["pr_auc"] = float(average_precision_score(y, scores)) if y.any() else float("nan")
    m["roc_auc"] = float(roc_auc_score(y, scores)) if 0 < y.sum() < len(y) else float("nan")
    m["positive_rate"] = float(np.mean(y))
    if probabilistic:
        m["brier"] = float(brier_score_loss(y, scores))
        m["brier_baseline_prevalence"] = float(brier_score_loss(y, np.full(len(y), np.mean(y))))
        curve, ece = calibration_curve(y, scores)
        m["ece"] = ece
        m["calibration_curve"] = curve
    return m


SUDDEN_EPISODE_MINUTES = 30  # episodes shorter than this (ramp <= 10 + hold) count as "sudden"


def event_records(segment: pd.DataFrame, pred: pd.Series, horizon: int) -> list[dict]:
    """One record per failure onset in the segment: detected?, lead time, fault type, speed.

    ``segment`` needs series_id and is_unhealthy; fault_type / episode_id are used when present.
    An onset is 'detected' if any alert fired in the H minutes before it; lead = minutes between
    the earliest such alert and the onset. Onsets with an incomplete warning window are skipped.
    """
    recs: list[dict] = []
    for sid, g in segment.groupby("series_id", sort=False):
        g = g.reset_index()  # original index kept in 'index'
        u = g["is_unhealthy"].to_numpy()
        onsets = np.flatnonzero(u & ~np.concatenate([[False], u[:-1]]))
        alert = g["index"].map(pred).fillna(False).to_numpy().astype(bool)
        for o in onsets:
            if o < horizon:
                continue
            win = alert[o - horizon : o]
            rec = {"series_id": sid, "onset": int(o), "detected": bool(win.any()),
                   "lead": int(horizon - np.argmax(win)) if win.any() else None}
            if "fault_type" in g:
                rec["fault_type"] = str(g["fault_type"].iloc[o])
            if "episode_id" in g and g["episode_id"].iloc[o] >= 0:
                ep = g["episode_id"].iloc[o]
                rec["speed"] = ("sudden" if int((g["episode_id"] == ep).sum()) < SUDDEN_EPISODE_MINUTES
                                else "gradual")
            recs.append(rec)
    return recs


def summarize_events(recs: list[dict], key: str | None = None) -> dict:
    """Aggregate records overall (key=None) or grouped by a record field."""
    def agg(rs: list[dict]) -> dict:
        leads = [r["lead"] for r in rs if r["lead"] is not None]
        det = sum(r["detected"] for r in rs)
        return {"failure_events": len(rs), "detected_events": det,
                "event_recall": det / len(rs) if rs else float("nan"),
                "median_lead_time_min": float(np.median(leads)) if leads else float("nan")}
    if key is None:
        return agg(recs)
    groups: dict[str, list[dict]] = {}
    for r in recs:
        if key in r:
            groups.setdefault(r[key], []).append(r)
    return {k: agg(v) for k, v in sorted(groups.items())}


def event_metrics(segment: pd.DataFrame, pred: pd.Series, horizon: int) -> dict:
    """Event-level summary plus the false-alert rate on the raw segment (see ``event_records``)."""
    out = summarize_events(event_records(segment, pred, horizon))
    days = max((segment["timestamp"].max() - segment["timestamp"].min()).total_seconds() / 86400, 1e-9)
    a = pred.reindex(segment.index).fillna(False).astype(bool)
    # false alert = alert on a healthy row whose horizon contains no failure onset
    false_alert_rows = int((a & ~segment["is_unhealthy"] & (segment["label_failure_within_h"] == 0)).sum())
    out["false_alert_rows_per_series_day"] = false_alert_rows / (days * segment["series_id"].nunique())
    return out
