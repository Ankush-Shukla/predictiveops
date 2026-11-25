"""Shared ingestion path: buffer -> score every new minute -> alert policy (used by HTTP + poller)."""

from __future__ import annotations

from app.api.schemas import IngestResponse
from app.api.state import ApiState
from app.models.predictor import Predictor
from app.models.schemas import MetricPoint
from app.services.series_buffer import IngestOutcome, SeriesBuffer

LEVEL_NUM = {"none": 0, "warn": 1, "alert": 2, "remediate": 3}



def _persist_accepted(state, series_id, outcome, source):
    """Persist accepted telemetry with its actual collection scope."""
    if not outcome.accepted_rows:
        return

    settings = state.settings
    host_id = settings.prometheus_host_series_id

    if series_id in {host_id, f"host:{host_id}"}:
        scope = "host"
    else:
        configured = settings.prometheus_application_services
        if isinstance(configured, str):
            configured = configured.split(",")
        application_ids = {
            str(item).strip() for item in configured if str(item).strip()
        }
        application_ids |= {f"application:{item}" for item in application_ids}
        scope = "application" if series_id in application_ids else "combined"

    state.telemetry_store.add_samples(
        scope=scope,
        series_id=series_id,
        rows=outcome.accepted_rows,
        ingestion_source=source,
    )


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



def collect_points(
    state: ApiState,
    series_id: str,
    samples: list[MetricPoint],
    predictor: Predictor | None = None,
) -> tuple[IngestOutcome, Exception | None]:
    """Collect telemetry and optionally score it under one state lock.

    Collection remains successful even if prediction fails. The returned
    exception represents an inference/policy failure, not a collection error.
    """
    with state.lock:
        buf = state.buffers.setdefault(
            series_id,
            SeriesBuffer(state.settings.ingest_buffer_minutes),
        )
        outcome = buf.add(samples)
        state.collectors.ingested.inc(outcome.accepted)
        _persist_accepted(state, series_id, outcome, "prometheus")

        if (
            predictor is None
            or not outcome.accepted
            or len(buf) < predictor.required_history
        ):
            return outcome, None

        try:
            predictions = predictor.predict_recent(buf.frame(), outcome.accepted)
            apply_policy(state, series_id, predictions)
        except Exception as exc:
            return outcome, exc

        return outcome, None


def ingest_points(state: ApiState, p: Predictor, series_id: str,
                  samples: list[MetricPoint]) -> IngestResponse:
    with state.lock:
        buf = state.buffers.setdefault(series_id, SeriesBuffer(state.settings.ingest_buffer_minutes))
        out = buf.add(samples)
        state.collectors.ingested.inc(out.accepted)
        _persist_accepted(state, series_id, out, "api")
        preds: list = []
        if out.accepted and len(buf) >= p.required_history:
            preds = p.predict_recent(buf.frame(), out.accepted)
        decisions = apply_policy(state, series_id, preds)
        return IngestResponse(
            series_id=series_id, accepted=out.accepted, rejected=out.rejected, gap_resets=out.gap_resets,
            buffered=len(buf), required_history=p.required_history,
            prediction=preds[-1] if preds else None, scored_samples=len(preds),
            decision=decisions[-1] if decisions else None)
