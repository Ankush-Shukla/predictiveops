"""Bounded per-series buffer of contiguous 1-minute samples (basis of live scoring)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pandas as pd

from app.core.domain import METRIC_COLUMNS
from app.models.schemas import MetricPoint

_STEP = pd.Timedelta(minutes=1)


@dataclass
class IngestOutcome:
    accepted: int = 0
    rejected: int = 0
    gap_resets: int = 0


class SeriesBuffer:
    """Keeps the most recent ``maxlen`` contiguous samples.

    Rules: samples at/before the newest stored timestamp are rejected (out-of-order or
    duplicate); a gap larger than one minute clears the history, because features require
    regular 1-minute spacing and silently bridging a gap would corrupt them.
    """

    def __init__(self, maxlen: int) -> None:
        self._rows: deque[dict] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._rows)

    def add(self, points: list[MetricPoint]) -> IngestOutcome:
        out = IngestOutcome()
        for p in sorted(points, key=lambda x: pd.Timestamp(x.timestamp)):
            ts = pd.Timestamp(p.timestamp)
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            if self._rows:
                last = self._rows[-1]["timestamp"]
                if ts <= last:
                    out.rejected += 1
                    continue
                if ts - last != _STEP:
                    self._rows.clear()
                    out.gap_resets += 1
            row = {"timestamp": ts, **{c: getattr(p, c) for c in METRIC_COLUMNS}}
            self._rows.append(row)
            out.accepted += 1
        return out

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(list(self._rows))
