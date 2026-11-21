"""Chronological train/validation/test split with a purge gap (prevents label leakage).

A row's label looks H minutes into the future. To stop training/validation labels from
peeking into the next segment, the last H minutes before each boundary are dropped from the
earlier segment. Splits are global in time (same cut-off timestamps for every series).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.core.exceptions import DataValidationError


@dataclass(frozen=True)
class SplitResult:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame
    cut1: pd.Timestamp
    cut2: pd.Timestamp


def chronological_split(
    df: pd.DataFrame, horizon_minutes: int, train_frac: float = 0.6, val_frac: float = 0.2
) -> SplitResult:
    if not 0 < train_frac < 1 or not 0 < val_frac < 1 or train_frac + val_frac >= 1:
        raise DataValidationError("invalid split fractions")
    t0, t1 = df["timestamp"].min(), df["timestamp"].max()
    span = t1 - t0
    cut1 = t0 + span * train_frac
    cut2 = t0 + span * (train_frac + val_frac)
    gap = pd.Timedelta(minutes=horizon_minutes)
    ts = df["timestamp"]
    train = df[ts < cut1 - gap]
    val = df[(ts >= cut1) & (ts < cut2 - gap)]
    test = df[ts >= cut2]
    if min(len(train), len(val), len(test)) == 0:
        raise DataValidationError("a split is empty; need more data")
    return SplitResult(train, val, test, cut1, cut2)
