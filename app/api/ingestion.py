"""Shared ingestion path: buffer -> score every new minute -> alert policy (used by HTTP + poller)."""

from __future__ import annotations

from app.api.schemas import IngestResponse
from app.api.state import ApiState
from app.models.predictor import Predictor
from app.models.schemas import MetricPoint
from app.services.series_buffer import SeriesBuffer

LEVEL_NUM = {"none": 0, "warn": 1, "alert": 2, "remediate": 3}


def apply_policy(state: ApiState, series_id: str, preds: list) -> list:
    """Run each newly scored minute through the policy engine, in time order; update metrics."""
    c, decisions = state.collectors, []
    for pr in preds:
        n_before = state.store.count_alerts()
        d = state.policy.evaluate(series_id, pr.probability, pr.threshold, pr.as_of, pr.model_version)
        decisions.append(d)
        if d.action_outcome:
            c.actions.labels(outcome=d.action_outcome).inc()
        if state.store.count_alerts() > n_before:
            c.alerts.labels(level=state.store.list_alerts(1)[0]["level"]).inc()
    if preds:
        last, d = preds[-1], decisions[-1]
        state.latest[series_id] = last
        c.probability.labels(series_id=series_id).set(last.probability)
        c.alert.labels(series_id=series_id).set(int(last.predicted_failure))
        c.policy_level.labels(series_id=series_id).set(LEVEL_NUM[d.level])
        c.predictions.labels(source="ingest").inc(len(preds))
    return decisions


def ingest_points(state: ApiState, p: Predictor, series_id: str,
                  samples: list[MetricPoint]) -> IngestResponse:
    with state.lock:
        buf = state.buffers.setdefault(series_id, SeriesBuffer(state.settings.ingest_buffer_minutes))
        out = buf.add(samples)
        state.collectors.ingested.inc(out.accepted)
        preds: list = []
        if out.accepted and len(buf) >= p.required_history:
            preds = p.predict_recent(buf.frame(), out.accepted)
        decisions = apply_policy(state, series_id, preds)
        return IngestResponse(
            series_id=series_id, accepted=out.accepted, rejected=out.rejected, gap_resets=out.gap_resets,
            buffered=len(buf), required_history=p.required_history,
            prediction=preds[-1] if preds else None, scored_samples=len(preds),
            decision=decisions[-1] if decisions else None)
