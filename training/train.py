"""Training pipeline: features -> chronological split -> fit -> calibrate -> evaluate -> gate."""

from __future__ import annotations

import platform
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.core.domain import METRIC_COLUMNS, SCOPE_METRIC_COLUMNS
from app.core.exceptions import DataValidationError
from app.core.logging import get_logger
from app.features.builder import FeatureBuilder, FeatureConfig
from app.models import registry
from app.models.bundle import ModelBundle
from app.models.schemas import validate_metrics_frame
from data.synthetic.generator import add_labels
from training import tracking
from training.baseline import DEFAULT_WARN_THRESHOLDS, threshold_score
from training.calibration import choose_and_fit_calibrator
from training.evaluate import (
    event_metrics,
    event_records,
    score_metrics,
    select_threshold,
    summarize_events,
)
from training.gates import GateConfig, evaluate_gates
from training.split import chronological_split

log = get_logger(__name__)
MODEL_TYPES = ("hgb", "logreg", "xgb")


@dataclass
class TrainConfig:
    model_type: str = "hgb"
    scope: str = "combined"
    horizon_minutes: int = 20
    seed: int = 42
    max_fpr_for_threshold: float = 0.05
    gates: GateConfig = field(default_factory=GateConfig)
    model_root: Path = Path("artifacts/models")
    use_mlflow: bool = True
    tracking_uri: str = "sqlite:///artifacts/mlflow.db"
    experiment: str = "predictiveops"
    registered_model_name: str = "predictiveops-failure-model"
    promote: bool = False
    version: str | None = None


@dataclass
class TrainingResult:
    version: str
    bundle: ModelBundle
    report: dict
    promoted: bool
    mlflow: dict | None


def make_estimator(model_type: str, seed: int, pos_weight: float):
    if model_type == "logreg":
        return make_pipeline(StandardScaler(), LogisticRegression(
            class_weight="balanced", C=0.5, max_iter=2000, random_state=seed))
    if model_type == "hgb":
        return HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.08, max_leaf_nodes=15, class_weight="balanced",
            early_stopping=False, random_state=seed)
    if model_type == "xgb":
        from xgboost import XGBClassifier

        return XGBClassifier(n_estimators=250, max_depth=4, learning_rate=0.08, subsample=0.8,
                             colsample_bytree=0.8, scale_pos_weight=pos_weight, eval_metric="aucpr",
                             random_state=seed, n_jobs=2, tree_method="hist")
    raise ValueError(f"unknown model_type {model_type!r}; choose from {MODEL_TYPES}")


def prepare(df: pd.DataFrame, horizon: int, *,
            metrics: tuple[str, ...] = METRIC_COLUMNS) -> tuple[pd.DataFrame, pd.DataFrame, FeatureBuilder]:
    """Return (df with labels recomputed for `horizon`, feature matrix, builder)."""
    validate_metrics_frame(df, metrics=metrics)
    if "is_unhealthy" not in df.columns:
        raise DataValidationError("dataset must contain 'is_unhealthy' ground truth")
    df = df.sort_values(["series_id", "timestamp"]).reset_index(drop=True)
    df = pd.concat([add_labels(g.drop(columns=["label_failure_within_h", "label_valid"],
                                      errors="ignore"), horizon)
                    for _, g in df.groupby("series_id", sort=False)]).reset_index(drop=True)
    builder = FeatureBuilder(FeatureConfig(metrics=metrics))
    return df, builder.transform(df), builder


def run_training(df: pd.DataFrame, cfg: TrainConfig) -> TrainingResult:
    np.random.seed(cfg.seed)
    if cfg.scope not in SCOPE_METRIC_COLUMNS:
        raise DataValidationError(f"unknown model scope {cfg.scope!r}; choose from {sorted(SCOPE_METRIC_COLUMNS)}")
    metrics = SCOPE_METRIC_COLUMNS[cfg.scope]
    df, feats, builder = prepare(df, cfg.horizon_minutes, metrics=metrics)
    data_source = "+".join(sorted(df["data_source"].unique())) if "data_source" in df else "unknown"

    scorable = df["label_valid"] & ~df["is_unhealthy"] & feats.notna().all(axis=1)
    sp = chronological_split(df, cfg.horizon_minutes)
    idx = {name: part.index[scorable.loc[part.index]] for name, part in
           (("train", sp.train), ("val", sp.val), ("test", sp.test))}
    y = {k: df.loc[i, "label_failure_within_h"].to_numpy() for k, i in idx.items()}
    X = {k: feats.loc[i, builder.feature_names] for k, i in idx.items()}
    for k in y:
        if len(np.unique(y[k])) < 2:
            raise DataValidationError(f"{k} split lacks both classes; generate more data")

    pos_w = float((y["train"] == 0).sum() / max((y["train"] == 1).sum(), 1))
    est = make_estimator(cfg.model_type, cfg.seed, pos_w).fit(X["train"], y["train"])
    raw = {k: est.predict_proba(X[k])[:, 1] for k in ("val", "test")}

    calibrator, cal_report = choose_and_fit_calibrator(raw["val"], y["val"])
    prob = {k: calibrator.predict(raw[k]) for k in raw}
    threshold, constraint_met = select_threshold(prob["val"], y["val"], cfg.max_fpr_for_threshold)

    m_val = score_metrics(y["val"], prob["val"], threshold, probabilistic=True)
    m_test = score_metrics(y["test"], prob["test"], threshold, probabilistic=True)

    # --- static-threshold baselines (default warning thresholds; and scale tuned on validation) ---
    baseline_thresholds = {m: DEFAULT_WARN_THRESHOLDS[m] for m in metrics if m in DEFAULT_WARN_THRESHOLDS}
    s_val = threshold_score(X["val"], baseline_thresholds)
    s_test = threshold_score(X["test"], baseline_thresholds)
    base_default = score_metrics(y["test"], s_test, 1.0, probabilistic=False)
    t_b, _ = select_threshold(s_val, y["val"], cfg.max_fpr_for_threshold)
    base_tuned = score_metrics(y["test"], s_test, t_b, probabilistic=False)

    # --- event-level view on the test segment ---
    pred = pd.Series(prob["test"] >= threshold, index=idx["test"])
    seg_cols = ["series_id", "timestamp", "is_unhealthy", "label_failure_within_h", "fault_type",
                "episode_id"]
    seg = df.loc[sp.test.index, [c for c in seg_cols if c in df.columns]]
    event = event_metrics(seg, pred, cfg.horizon_minutes)
    recs = event_records(seg, pred, cfg.horizon_minutes)
    event_by_fault, event_by_speed = summarize_events(recs, "fault_type"), summarize_events(recs, "speed")

    # --- explainability: global permutation importance on a validation sample ---
    rng = np.random.default_rng(cfg.seed)
    take = rng.choice(len(X["val"]), size=min(4000, len(X["val"])), replace=False)
    pi = permutation_importance(est, X["val"].iloc[take], y["val"][take], scoring="average_precision",
                                n_repeats=3, random_state=cfg.seed, n_jobs=1)
    order = np.argsort(pi.importances_mean)[::-1][:15]
    top = [{"feature": builder.feature_names[i], "importance": float(pi.importances_mean[i])}
           for i in order]

    gates = evaluate_gates(m_test, event, base_default["pr_auc"], cfg.gates)
    version = cfg.version or f"v{datetime.now(UTC):%Y%m%d%H%M%S}-{cfg.scope}-{cfg.model_type}"
    report = {
        "val": m_val, "test": m_test, "event": event, "event_by_fault": event_by_fault,
        "event_by_speed": event_by_speed,
        "baseline_default": base_default, "baseline_tuned": base_tuned,
        "calibration": cal_report, "threshold_fpr_constraint_met": constraint_met,
        "top_features_permutation": top, "gates": gates.model_dump(),
        "split": {"train_rows": int(len(y["train"])), "val_rows": int(len(y["val"])),
                  "test_rows": int(len(y["test"])), "cut_train_val": str(sp.cut1),
                  "cut_val_test": str(sp.cut2), "purge_gap_minutes": cfg.horizon_minutes},
    }
    metadata = {
        "version": version, "model_type": cfg.model_type, "scope": cfg.scope,
        "horizon_minutes": cfg.horizon_minutes, "metric_columns": list(metrics),
        "data_source": data_source, "seed": cfg.seed,
        "trained_at": datetime.now(UTC).isoformat(),
        "evaluation": {"test": {k: v for k, v in m_test.items() if k != "calibration_curve"},
                       "event": event, "event_by_fault": event_by_fault, "event_by_speed": event_by_speed,
                       "gates": gates.model_dump(),
                       "baseline_default_pr_auc": base_default["pr_auc"]},
        "top_features_permutation": top,
        "environment": {"python": platform.python_version(), "sklearn": sklearn.__version__},
        "disclaimer": ("Evaluated on a held-out chronological test segment of "
                       f"{data_source!r} data only; not evidence of real-world performance."),
    }
    bundle = ModelBundle(est, calibrator, float(threshold), builder.schema, metadata)
    registry.save_version(bundle, cfg.model_root, version)

    promoted, ml = False, None
    if cfg.use_mlflow:
        ml = tracking.log_training_run(
            tracking_uri=cfg.tracking_uri, experiment=cfg.experiment, run_name=version,
            params={"model_type": cfg.model_type, "scope": cfg.scope, "horizon_minutes": cfg.horizon_minutes,
                    "seed": cfg.seed, "data_source": data_source, "threshold": threshold,
                    "schema_hash": builder.schema.schema_hash, "n_features": len(builder.feature_names),
                    **{f"gate_{k}": v for k, v in asdict(cfg.gates).items()}},
            report=report, bundle_dir=cfg.model_root / version,
            register_as=cfg.registered_model_name)
    if cfg.promote and gates.passed:
        registry.set_champion(cfg.model_root, version)
        promoted = True
        if ml and ml["registered_version"]:
            tracking.set_champion_alias(cfg.tracking_uri, cfg.registered_model_name,
                                        ml["registered_version"])
    log.info("training complete", extra={"version": version, "gates_passed": gates.passed,
                                         "promoted": promoted, "test_f1": m_test["f1"]})
    return TrainingResult(version, bundle, report, promoted, ml)
