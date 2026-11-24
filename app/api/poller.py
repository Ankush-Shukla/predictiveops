"""Background poller: Prometheus -> sample clock -> shared ingestion path."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from app.api.deps import production_model_block_reason
from app.api.ingestion import ingest_points
from app.api.state import ApiState
from app.core.logging import get_logger
from app.models.schemas import MetricPoint
from app.services.prometheus_client import PrometheusClient, frame_to_points

log = get_logger(__name__)


class PrometheusPoller:
    def __init__(self, state: ApiState, client: PrometheusClient, services: list[str], *,
                 step_seconds: float, interval_seconds: float, lookback_steps: int,
                 clock=time.time) -> None:
        self.state, self.client, self.services = state, client, services
        self.step, self.interval, self.steps = step_seconds, interval_seconds, lookback_steps
        self._clock = clock
        self._sample_origin_ts = self._clock()
        self._sample_origin_dt = datetime.now(UTC)
        self.status: dict[str, dict] = {s: {"last_poll": None, "last_ok": None, "error": None,
                                            "samples_ingested": 0} for s in services}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> None:
        for svc in self.services:
            st = self.status[svc]
            st["last_poll"] = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                reason = production_model_block_reason(self.state)
                if reason is not None:
                    raise RuntimeError(reason)
                predictor = self.state.predictor
                if predictor is None:
                    raise RuntimeError("no champion model loaded")
                raw = self.client.fetch_window(svc, end=self._clock(), steps=self.steps,
                                               step_seconds=self.step)
                df = self.client.to_sample_clock(
                    raw,
                    self.step,
                    origin_ts=self._sample_origin_ts,
                    origin_sample_ts=self._sample_origin_dt,
                )
                if df.empty:
                    raise RuntimeError("no complete samples returned for this service")
                res = ingest_points(self.state, predictor, svc,
                                    [MetricPoint(**p) for p in frame_to_points(df)])
                st["samples_ingested"] += res.accepted
                st["last_ok"], st["error"] = st["last_poll"], None
            except Exception as exc:  # a failing source must never stop the poller or the API
                st["error"] = f"{type(exc).__name__}: {exc}"
                log.warning("prometheus poll failed", extra={"service": svc, "error": st["error"]})

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="prometheus-poller")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
