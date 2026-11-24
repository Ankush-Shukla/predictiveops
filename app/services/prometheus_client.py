"""Minimal Prometheus HTTP API client that builds model-ready metric windows."""

from __future__ import annotations

import re
from datetime import datetime

import httpx
import pandas as pd

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import PredictiveOpsError

SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# One expression per model metric. `$service` is replaced by a VALIDATED service name only.
# These read the demo target's SIMULATED gauges; to use real Node Exporter / application metrics,
# override the queries AND retrain on data with the same scales (see README).
DEFAULT_QUERIES: dict[str, str] = {m: f'avg(demo_{m}{{service="$service"}})' for m in METRIC_COLUMNS}


class PrometheusError(PredictiveOpsError):
    """Prometheus is unreachable, returned an error, or returned unusable data."""


class PrometheusClient:
    def __init__(self, base_url: str, *, timeout: float = 5.0, queries: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.queries = queries or DEFAULT_QUERIES
        missing = set(METRIC_COLUMNS) - set(self.queries)
        if missing:
            raise ValueError(f"queries missing for metrics: {sorted(missing)}")
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def healthy(self) -> bool:
        try:
            return self._http.get("/-/healthy").status_code == 200
        except httpx.HTTPError:
            return False

    def query_range(self, promql: str, start: float, end: float, step: float) -> dict[float, float]:
        try:
            r = self._http.get("/api/v1/query_range", params={
                "query": promql, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step:g}"})
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PrometheusError(f"prometheus request failed: {exc}") from exc
        if r.status_code != 200 or body.get("status") != "success":
            raise PrometheusError(f"prometheus error: {body.get('error', r.status_code)}")
        result = body["data"]["result"]
        if len(result) > 1:
            raise PrometheusError("query returned several series; wrap it in avg()/max()")
        if not result:
            return {}
        return {float(t): float(v) for t, v in result[0]["values"]}

    def fetch_window(self, service: str, *, end: float, steps: int, step_seconds: float) -> pd.DataFrame:
        """Aligned frame (real timestamps in seconds) holding only steps where ALL 8 metrics exist."""
        if not SERVICE_RE.fullmatch(service):
            raise PrometheusError(f"invalid service name {service!r}")
        start = end - (steps - 1) * step_seconds
        cols = {m: self.query_range(q.replace("$service", service), start, end, step_seconds)
                for m, q in self.queries.items()}
        df = pd.DataFrame(cols).dropna().sort_index()
        df.index.name = "ts"
        return df.reset_index()

    @staticmethod
    def to_sample_clock(df: pd.DataFrame, step_seconds: float) -> pd.DataFrame:
        """Map each scrape step to one logical 1-minute sample with a STABLE timestamp.

        logical = floor(ts / step) * 60 s, so repeated polls label the same step identically and
        accelerated demos (step < 60 s) still produce the regular 1-minute spacing the model needs.
        """
        out = df.copy()
        idx = (out["ts"] // step_seconds).astype("int64") * 60
        out["timestamp"] = pd.to_datetime(idx, unit="s", utc=True)
        return out.drop(columns=["ts"]).drop_duplicates("timestamp").reset_index(drop=True)


def frame_to_points(df: pd.DataFrame) -> list[dict]:
    return [{"timestamp": r["timestamp"].to_pydatetime(), **{m: float(r[m]) for m in METRIC_COLUMNS}}
            for _, r in df.iterrows()]


def now_ts(dt: datetime | None = None) -> float:
    return (dt or datetime.now().astimezone()).timestamp()
