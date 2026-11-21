"""Feature engineering shared by training AND inference (single source of truth).

All features are strictly backward-looking (current and past samples only), so a feature
vector at time t never uses information from after t. Input must be 1-minute, time-ordered
series; features are computed independently per ``series_id``.

Per metric (in ``METRIC_COLUMNS`` order) the feature names are, in this fixed order:
    raw, lag_<k>, roc_<k> (per-minute rate of change), then for each window w:
    mean_<w>, std_<w>, min_<w>, max_<w>, and finally slope_<w> (least-squares slope/min).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from pydantic import BaseModel

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import DataValidationError

FEATURE_VERSION = "1"


@dataclass(frozen=True)
class FeatureConfig:
    windows: tuple[int, ...] = (5, 15, 30)
    lags: tuple[int, ...] = (1, 5, 15)
    roc_lags: tuple[int, ...] = (5, 15)
    slope_window: int = 30

    @property
    def required_history(self) -> int:
        """Minimum number of rows needed to compute one complete feature vector."""
        return max(*self.windows, *self.lags, *self.roc_lags, self.slope_window)


class FeatureSchema(BaseModel):
    """Ordered feature names + config; its hash guards against train/inference drift."""

    version: str
    names: list[str]
    config: dict
    schema_hash: str


def _names(cfg: FeatureConfig) -> list[str]:
    out: list[str] = []
    for m in METRIC_COLUMNS:
        out.append(f"{m}__raw")
        out += [f"{m}__lag_{k}" for k in cfg.lags]
        out += [f"{m}__roc_{k}" for k in cfg.roc_lags]
        for w in cfg.windows:
            out += [f"{m}__{s}_{w}" for s in ("mean", "std", "min", "max")]
        out.append(f"{m}__slope_{cfg.slope_window}")
    return out


def _slope(x: np.ndarray, w: int) -> np.ndarray:
    """Rolling least-squares slope (units per step); NaN for the first w-1 rows."""
    out = np.full(len(x), np.nan)
    if len(x) < w:
        return out
    j = np.arange(w) - (w - 1) / 2.0
    weights = j / np.sum(j**2)
    out[w - 1 :] = sliding_window_view(x, w) @ weights
    return out


class FeatureBuilder:
    def __init__(self, config: FeatureConfig | None = None) -> None:
        self.config = config or FeatureConfig()
        self.feature_names: list[str] = _names(self.config)

    @property
    def schema(self) -> FeatureSchema:
        cfg = asdict(self.config)
        payload = json.dumps({"v": FEATURE_VERSION, "names": self.feature_names, "cfg": cfg},
                             sort_keys=True, default=list)
        return FeatureSchema(version=FEATURE_VERSION, names=self.feature_names, config=cfg,
                             schema_hash=hashlib.sha256(payload.encode()).hexdigest())

    # ---- internals ----
    def _one_series(self, g: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        cols: dict[str, np.ndarray | pd.Series] = {}
        for m in METRIC_COLUMNS:
            s = g[m].astype(float).reset_index(drop=True)
            cols[f"{m}__raw"] = s
            for k in cfg.lags:
                cols[f"{m}__lag_{k}"] = s.shift(k)
            for k in cfg.roc_lags:
                cols[f"{m}__roc_{k}"] = (s - s.shift(k)) / k
            for w in cfg.windows:
                r = s.rolling(w, min_periods=w)
                cols[f"{m}__mean_{w}"] = r.mean()
                cols[f"{m}__std_{w}"] = r.std(ddof=0)
                cols[f"{m}__min_{w}"] = r.min()
                cols[f"{m}__max_{w}"] = r.max()
            cols[f"{m}__slope_{cfg.slope_window}"] = _slope(s.to_numpy(), cfg.slope_window)
        out = pd.DataFrame(cols)[self.feature_names]
        out.index = g.index
        return out

    @staticmethod
    def _check(df: pd.DataFrame) -> None:
        missing = [c for c in ("timestamp", *METRIC_COLUMNS) if c not in df.columns]
        if missing:
            raise DataValidationError(f"missing columns: {missing}")
        d = df["timestamp"].diff().dropna()
        if not d.empty and (d != pd.Timedelta(minutes=1)).any():
            raise DataValidationError("series must be sorted with a regular 1-minute spacing")

    # ---- public API ----
    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Feature matrix aligned to ``df.index``. Warm-up rows (insufficient history) contain NaN."""
        if "series_id" in df.columns:
            parts = []
            for _, g in df.groupby("series_id", sort=False):
                self._check(g)
                parts.append(self._one_series(g))
            return pd.concat(parts).loc[df.index]
        self._check(df)
        return self._one_series(df)

    def transform_last(self, window: pd.DataFrame) -> pd.DataFrame:
        """Single-row feature frame for the most recent sample of ONE series (inference)."""
        need = self.config.required_history
        if len(window) < need:
            raise DataValidationError(f"need at least {need} rows of history, got {len(window)}")
        tail = window.iloc[-need:]
        self._check(tail)
        feats = self._one_series(tail)
        last = feats.iloc[[-1]]
        if last.isna().any(axis=None):
            raise DataValidationError("insufficient history to compute all features")
        return last
