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


def event_metrics(segment: pd.DataFrame, pred: pd.Series, horizon: int) -> dict:
    """Event-level view on a raw (unfiltered) segment.

    ``segment`` has columns series_id, is_unhealthy and index aligned with ``pred`` for kept
    (scorable) rows only. An onset is 'detected' if any alert fired in the H minutes before it;
    lead time = minutes between the earliest such alert and the onset.
    """
    detected, leads, total = 0, [], 0
    for _, g in segment.groupby("series_id", sort=False):
        g = g.reset_index()  # keep original index in 'index'
        u = g["is_unhealthy"].to_numpy()
        onsets = np.flatnonzero(u & ~np.concatenate([[False], u[:-1]]))
        alert = g["index"].map(pred).fillna(False).to_numpy().astype(bool)
        for o in onsets:
            if o < horizon:  # incomplete warning window at segment start
                continue
            total += 1
            win = alert[o - horizon : o]
            if win.any():
                detected += 1
                leads.append(int(horizon - np.argmax(win)))
    days = max((segment["timestamp"].max() - segment["timestamp"].min()).total_seconds() / 86400, 1e-9)
    n_series = segment["series_id"].nunique()
    a = pred.reindex(segment.index).fillna(False).astype(bool)
    # false alert = alert on a healthy row whose horizon contains no failure onset
    false_alert_rows = int((a & ~segment["is_unhealthy"] & (segment["label_failure_within_h"] == 0)).sum())
    return {
        "failure_events": total,
        "detected_events": detected,
        "event_recall": detected / total if total else float("nan"),
        "median_lead_time_min": float(np.median(leads)) if leads else float("nan"),
        "false_alert_rows_per_series_day": false_alert_rows / (days * n_series),
    }
