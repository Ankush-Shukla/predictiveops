"""Probability calibration fitted on VALIDATION data only (never train or test)."""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss


class ProbabilityCalibrator:
    """Maps raw model scores to calibrated probabilities (isotonic or Platt/sigmoid)."""

    def __init__(self, method: str = "sigmoid") -> None:
        if method not in {"sigmoid", "isotonic"}:
            raise ValueError("method must be 'sigmoid' or 'isotonic'")
        self.method = method
        self._m: IsotonicRegression | LogisticRegression | None = None

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, scores: np.ndarray, y: np.ndarray) -> ProbabilityCalibrator:
        if len(np.unique(y)) < 2:
            raise ValueError("calibration needs both classes in the validation data")
        if self.method == "isotonic":
            self._m = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(scores, y)
        else:
            self._m = LogisticRegression(C=1e6, max_iter=1000).fit(self._logit(scores), y)
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        if self._m is None:
            raise RuntimeError("calibrator not fitted")
        if self.method == "isotonic":
            return np.asarray(self._m.predict(scores), dtype=float)
        return self._m.predict_proba(self._logit(scores))[:, 1]


def choose_and_fit_calibrator(scores: np.ndarray, y: np.ndarray) -> tuple[ProbabilityCalibrator, dict]:
    """Pick the method by out-of-sample Brier on the chronologically later half of validation,
    then refit the winner on the whole validation set."""
    half = len(scores) // 2
    report: dict[str, float] = {}
    best, best_b = "sigmoid", np.inf
    if len(np.unique(y[:half])) == 2 and len(np.unique(y[half:])) == 2:
        for method in ("sigmoid", "isotonic"):
            cal = ProbabilityCalibrator(method).fit(scores[:half], y[:half])
            b = float(brier_score_loss(y[half:], cal.predict(scores[half:])))
            report[f"brier_{method}"] = b
            if b < best_b:
                best, best_b = method, b
    report["chosen"] = best  # type: ignore[assignment]
    return ProbabilityCalibrator(best).fit(scores, y), report
