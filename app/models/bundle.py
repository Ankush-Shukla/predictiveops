"""Model bundle: everything needed to score, persisted together and verified on load.

SECURITY NOTE: bundles are joblib (pickle) files. Only load bundles produced by your own
training pipeline; never load untrusted artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import PredictiveOpsError
from app.features.builder import FeatureBuilder, FeatureConfig, FeatureSchema

MODEL_FILE = "model.joblib"
META_FILE = "metadata.json"


class ModelSchemaMismatchError(PredictiveOpsError):
    """The bundle's feature schema differs from the feature code in this build."""


class ModelNotFoundError(PredictiveOpsError):
    """No model artifact is available."""


@dataclass
class ModelBundle:
    estimator: Any  # fitted sklearn-compatible classifier with predict_proba
    calibrator: Any  # ProbabilityCalibrator
    threshold: float
    schema: FeatureSchema
    metadata: dict = field(default_factory=dict)

    def feature_builder(self) -> FeatureBuilder:
        cfg = self.schema.config
        fb = FeatureBuilder(FeatureConfig(
            windows=tuple(cfg["windows"]), lags=tuple(cfg["lags"]),
            roc_lags=tuple(cfg["roc_lags"]), slope_window=cfg["slope_window"],
            metrics=tuple(cfg.get("metrics", METRIC_COLUMNS))))
        if fb.schema.schema_hash != self.schema.schema_hash:
            raise ModelSchemaMismatchError(
                "feature schema hash mismatch: model was trained with different feature "
                "definitions than the current code")
        return fb

    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump({"estimator": self.estimator, "calibrator": self.calibrator,
                     "threshold": self.threshold, "schema": self.schema.model_dump()},
                    directory / MODEL_FILE)
        meta = dict(self.metadata, threshold=self.threshold, schema_hash=self.schema.schema_hash,
                    feature_names=self.schema.names)
        (directory / META_FILE).write_text(json.dumps(meta, indent=2, default=str))
        return directory

    @classmethod
    def load(cls, directory: Path) -> ModelBundle:
        directory = Path(directory)
        f = directory / MODEL_FILE
        if not f.exists():
            raise ModelNotFoundError(f"no model at {directory}")
        raw = joblib.load(f)
        meta_path = directory / META_FILE
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        bundle = cls(raw["estimator"], raw["calibrator"], float(raw["threshold"]),
                     FeatureSchema(**raw["schema"]), meta)
        bundle.feature_builder()  # validates schema parity eagerly
        return bundle
