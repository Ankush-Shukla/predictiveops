"""Minimal Prometheus HTTP API client that builds model-ready metric windows."""

from __future__ import annotations

import math
import re
from datetime import datetime

import httpx
import pandas as pd

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import PredictiveOpsError

SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# Legacy development-only combined-model queries. Production uses HOST_QUERIES and
# APPLICATION_QUERIES below; the production Compose stack has no demo target.
DEFAULT_QUERIES: dict[str, str] = {m: f'avg(demo_{m}{{service="$service"}})' for m in METRIC_COLUMNS}

HOST_QUERIES: dict[str, str] = {
    "cpu_pct": '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100',
    "memory_pct": '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
    "disk_io_util_pct": '100 * max(rate(node_disk_io_time_seconds_total[5m]))',
    "network_rx_mbps": (
        'sum(rate(node_network_receive_bytes_total{'
        'device!~"lo|veth.*|docker.*|br-.*|tailscale.*|tun.*|wg.*"}[5m])) * 8 / 1e6'
    ),
    "network_tx_mbps": (
        'sum(rate(node_network_transmit_bytes_total{'
        'device!~"lo|veth.*|docker.*|br-.*|tailscale.*|tun.*|wg.*"}[5m])) * 8 / 1e6'
    ),
}

APPLICATION_QUERIES: dict[str, str] = {
    "cpu_pct": (
        '100 * sum(rate(container_cpu_usage_seconds_total{'
        'container_label_predictiveops_service="$service",image!=""}[5m])) '
        '/ scalar(count(node_cpu_seconds_total{mode="idle"}))'
    ),
    "memory_pct": (
        '100 * sum(container_memory_working_set_bytes{'
        'container_label_predictiveops_service="$service",image!=""}) '
        '/ scalar(node_memory_MemTotal_bytes)'
    ),
    "disk_io_util_pct": (
        '100 * max(rate(container_fs_io_time_seconds_total{'
        'container_label_predictiveops_service="$service",image!=""}[5m]))'
    ),
    "network_rx_mbps": (
        'sum(rate(container_network_receive_bytes_total{'
        'container_label_predictiveops_service="$service",image!=""}[5m])) * 8 / 1e6'
    ),
    "network_tx_mbps": (
        'sum(rate(container_network_transmit_bytes_total{'
        'container_label_predictiveops_service="$service",image!=""}[5m])) * 8 / 1e6'
    ),
    "request_rate_rps": 'sum(rate(predictiveops_app_requests_total{service="$service"}[5m]))',
    "latency_p95_ms": (
        'histogram_quantile(0.95, sum by (le) (rate('
        'predictiveops_app_request_duration_seconds_bucket{service="$service"}[5m]))) * 1000'
    ),
    "error_rate": (
        '(sum(rate(predictiveops_app_requests_total{service="$service",status=~"5.."}[5m])) '
        'or vector(0)) '
        '/ clamp_min(sum(rate(predictiveops_app_requests_total{service="$service"}[5m])), 1e-9)'
    ),
}


class PrometheusError(PredictiveOpsError):
    """Prometheus is unreachable, returned an error, or returned unusable data."""


class PrometheusClient:
    def __init__(self, base_url: str, *, timeout: float = 5.0, queries: dict[str, str] | None = None,
                 required_metrics: tuple[str, ...] = METRIC_COLUMNS,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.queries = queries or DEFAULT_QUERIES
        self.metrics = tuple(self.queries)
        missing = set(required_metrics) - set(self.queries)
        if missing:
            raise ValueError(f"queries missing for required metrics: {sorted(missing)}")
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
        """Aligned frame containing only sample steps where every configured query has a value."""
        if not SERVICE_RE.fullmatch(service):
            raise PrometheusError(f"invalid service name {service!r}")
        aligned_end = math.floor(end / step_seconds) * step_seconds
        start = aligned_end - (steps - 1) * step_seconds
        end = aligned_end
        cols = {m: self.query_range(q.replace("$service", service), start, end, step_seconds)
                for m, q in self.queries.items()}
        df = pd.DataFrame(cols).dropna().sort_index()
        df.index.name = "ts"
        return df.reset_index()
    @staticmethod
    def to_sample_clock(
        df: pd.DataFrame,
        step_seconds: float,
        *,
        origin_ts: float,
        origin_sample_ts: datetime,
    ) -> pd.DataFrame:
        """Map accelerated Prometheus steps to stable logical minute timestamps."""
        out = df.copy()
        elapsed = ((out["ts"] - origin_ts) // step_seconds).astype("int64")
        origin = pd.Timestamp(origin_sample_ts)
        if origin.tzinfo is None:
            origin = origin.tz_localize("UTC")
        else:
            origin = origin.tz_convert("UTC")
        out["timestamp"] = origin + pd.to_timedelta(elapsed * 60, unit="s")
        return out.drop(columns=["ts"]).drop_duplicates("timestamp").reset_index(drop=True)


def frame_to_points(df: pd.DataFrame) -> list[dict]:
    """Build the shared ingestion schema; unused fields are neutral placeholders and are excluded
    by the scope-specific model feature schema."""
    return [
        {"timestamp": r["timestamp"].to_pydatetime(),
         **{m: float(r[m]) if m in df.columns else 0.0 for m in METRIC_COLUMNS}}
        for _, r in df.iterrows()
    ]


def now_ts(dt: datetime | None = None) -> float:
    return (dt or datetime.now().astimezone()).timestamp()
