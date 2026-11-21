"""MLflow pyfunc wrapper so the bundle can be logged/registered as a proper MLflow model."""

from __future__ import annotations

import mlflow.pyfunc
import pandas as pd


class PredictiveOpsPyfunc(mlflow.pyfunc.PythonModel):
    """Input: a single-series metric window DataFrame. Output: one-row probability frame."""

    def load_context(self, context) -> None:
        from app.models.bundle import ModelBundle
        from app.models.predictor import Predictor

        self._predictor = Predictor(ModelBundle.load(context.artifacts["bundle"]), "mlflow")

    def predict(self, context, model_input: pd.DataFrame, params=None) -> pd.DataFrame:
        r = self._predictor.predict(model_input)
        return pd.DataFrame([{"probability": r.probability, "predicted_failure": r.predicted_failure}])
