"""Inference: metric window -> calibrated failure probability."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import DataValidationError
from app.models.bundle import ModelBundle
from app.models.registry import load_champion
from app.models.schemas import PredictionResult, validate_metrics_frame


class Predictor:
    def __init__(self, bundle: ModelBundle, version: str) -> None:
        self.bundle = bundle
        self.version = version
        self._builder = bundle.feature_builder()
        self.metric_columns = tuple(bundle.schema.config.get("metrics", METRIC_COLUMNS))

    @classmethod
    def from_champion(cls, model_root: Path) -> Predictor:
        version, bundle = load_champion(model_root)
        return cls(bundle, version)

    @property
    def required_history(self) -> int:
        return self._builder.config.required_history

    def predict_proba_frame(self, features: pd.DataFrame) -> pd.Series:
        """Calibrated probabilities for a feature matrix (column order enforced)."""
        x = features[self._builder.feature_names]
        raw = self.bundle.estimator.predict_proba(x)[:, 1]
        return pd.Series(self.bundle.calibrator.predict(raw), index=features.index)

    def _result(self, prob: float, ts: pd.Timestamp) -> PredictionResult:
        meta = self.bundle.metadata
        return PredictionResult(
            probability=min(max(float(prob), 0.0), 1.0), threshold=self.bundle.threshold,
            predicted_failure=prob >= self.bundle.threshold,
            horizon_minutes=int(meta.get("horizon_minutes", 0)) or 20,
            model_version=self.version, data_source=str(meta.get("data_source", "unknown")),
            as_of=ts.to_pydatetime())

    def predict_recent(self, window: pd.DataFrame, n: int) -> list[PredictionResult]:
        """Score the last ``n`` samples (those with complete features) of ONE contiguous series."""
        validate_metrics_frame(window, require_series_id=False, metrics=self.metric_columns)
        window = window.sort_values("timestamp").reset_index(drop=True)
        feats = self._builder.transform(window)
        idx = feats.index[feats.notna().all(axis=1)][-max(n, 0):] if n > 0 else feats.index[:0]
        if len(idx) == 0:
            return []
        probs = self.predict_proba_frame(feats.loc[idx])
        return [self._result(probs.loc[i], window.loc[i, "timestamp"]) for i in idx]

    def predict(self, window: pd.DataFrame) -> PredictionResult:
        """Score the latest sample of ONE series given >= required_history rows of history."""
        if "series_id" in window.columns and window["series_id"].nunique() > 1:
            raise DataValidationError("predict() takes a single series")
        validate_metrics_frame(window, require_series_id=False, metrics=self.metric_columns)
        window = window.sort_values("timestamp").reset_index(drop=True)
        feats = self._builder.transform_last(window)
        p = float(self.predict_proba_frame(feats).iloc[0])
        return self._result(p, window["timestamp"].iloc[-1])
