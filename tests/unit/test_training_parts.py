import numpy as np
import pandas as pd
import pytest

from app.core.exceptions import DataValidationError
from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.calibration import ProbabilityCalibrator, choose_and_fit_calibrator
from training.evaluate import (
    calibration_curve,
    confusion_metrics,
    event_metrics,
    select_threshold,
)
from training.gates import GateConfig, evaluate_gates
from training.split import chronological_split


def test_split_is_chronological_with_purge_gap():
    df = generate_dataset(GeneratorConfig(days=5, n_series=2, seed=1))
    h = 20
    sp = chronological_split(df, h)
    assert sp.train.timestamp.max() < sp.cut1 - pd.Timedelta(minutes=h) + pd.Timedelta(seconds=1)
    assert sp.val.timestamp.min() >= sp.cut1 and sp.test.timestamp.min() >= sp.cut2
    assert sp.val.timestamp.max() < sp.cut2 - pd.Timedelta(minutes=h) + pd.Timedelta(seconds=1)
    assert sp.train.timestamp.max() < sp.val.timestamp.min()
    assert sp.val.timestamp.max() < sp.test.timestamp.min()
    assert not (set(sp.train.index) & set(sp.val.index) | set(sp.val.index) & set(sp.test.index))
    # no train/val label horizon reaches into the next segment
    assert (sp.train.timestamp + pd.Timedelta(minutes=h) < sp.cut1).all()
    assert (sp.val.timestamp + pd.Timedelta(minutes=h) < sp.cut2).all()


def test_split_rejects_bad_fractions():
    df = generate_dataset(GeneratorConfig(days=2, n_series=1))
    with pytest.raises(DataValidationError):
        chronological_split(df, 20, 0.8, 0.3)


def test_confusion_metrics():
    m = confusion_metrics(np.array([1, 1, 0, 0, 0]), np.array([1, 0, 1, 0, 0]))
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 2)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["f1"] == 0.5
    assert np.isclose(m["fpr"], 1 / 3)


def test_select_threshold_respects_fpr_cap():
    rng = np.random.default_rng(0)
    y = (rng.random(5000) < 0.1).astype(int)
    s = np.clip(0.3 * y + rng.normal(0.3, 0.15, 5000), 0, 1)
    t, ok = select_threshold(s, y, max_fpr=0.05)
    assert ok and confusion_metrics(y, s >= t)["fpr"] <= 0.05
    t2, ok2 = select_threshold(s, y, max_fpr=0.0)
    assert isinstance(t2, float)  # falls back gracefully (may or may not be feasible)


def test_calibration_improves_overconfident_scores():
    rng = np.random.default_rng(1)
    true_p = rng.random(6000) * 0.3
    y = (rng.random(6000) < true_p).astype(int)
    overconf = np.clip(true_p * 3, 0, 1)
    for method in ("sigmoid", "isotonic"):
        cal = ProbabilityCalibrator(method).fit(overconf[:4000], y[:4000])
        _, ece_after = calibration_curve(y[4000:], cal.predict(overconf[4000:]))
        _, ece_before = calibration_curve(y[4000:], overconf[4000:])
        assert ece_after < ece_before
    cal, rep = choose_and_fit_calibrator(overconf, y)
    assert rep["chosen"] in ("sigmoid", "isotonic")
    with pytest.raises(ValueError):
        ProbabilityCalibrator().fit(overconf, np.zeros_like(y))


def test_event_metrics_detection_and_lead_time():
    n, h = 200, 20
    u = np.zeros(n, dtype=bool)
    u[100:110] = True  # failure with onset at 100
    u[170:175] = True  # second failure with onset at 170
    seg = pd.DataFrame({"series_id": "a", "is_unhealthy": u,
                        "timestamp": pd.date_range("2025-01-01", periods=n, freq="min"),
                        "label_failure_within_h": 0})
    pred = pd.Series(False, index=seg.index)
    pred[88] = True  # alert 12 min before first onset; nothing for the second
    pred[10] = True  # a false alert
    scorable = ~seg.is_unhealthy
    pred = pred[scorable]
    m = event_metrics(seg, pred, h)
    assert m["failure_events"] == 2 and m["detected_events"] == 1
    assert m["event_recall"] == 0.5 and m["median_lead_time_min"] == 12
    assert m["false_alert_rows_per_series_day"] > 0


def test_gates():
    ok = {"f1": 0.8, "fpr": 0.02, "pr_auc": 0.9}
    ev = {"event_recall": 0.9}
    assert evaluate_gates(ok, ev, 0.5, GateConfig()).passed
    assert not evaluate_gates({**ok, "f1": 0.6}, ev, 0.5, GateConfig()).passed
    assert not evaluate_gates({**ok, "fpr": 0.2}, ev, 0.5, GateConfig()).passed
    assert not evaluate_gates(ok, ev, 0.95, GateConfig()).passed
    assert not evaluate_gates(ok, {"event_recall": 0.1}, 0.5, GateConfig(min_event_recall=0.5)).passed
