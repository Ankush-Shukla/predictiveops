from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_predictor, get_state, require_api_key
from app.api.schemas import IngestRequest, IngestResponse, PredictRequest, SeriesStatus
from app.api.state import ApiState
from app.models.predictor import Predictor
from app.models.schemas import PredictionResult, points_to_frame
from app.services.series_buffer import SeriesBuffer

router = APIRouter(prefix="/v1", tags=["prediction"])


@router.post("/predict", response_model=PredictionResult)
def predict(req: PredictRequest, p: Predictor = Depends(get_predictor),
            state: ApiState = Depends(get_state)) -> PredictionResult:
    """Score the latest sample of one service from a window of recent 1-minute samples."""
    result = p.predict(points_to_frame(req.samples))
    state.collectors.predictions.labels(source="predict").inc()
    return result


LEVEL_NUM = {"none": 0, "warn": 1, "alert": 2, "remediate": 3}


def apply_policy(state: ApiState, p: Predictor, series_id: str, preds: list) -> list:
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


@router.post("/metrics/ingest", response_model=IngestResponse, dependencies=[Depends(require_api_key)])
def ingest(req: IngestRequest, p: Predictor = Depends(get_predictor),
           state: ApiState = Depends(get_state)) -> IngestResponse:
    """Append samples to a series buffer, score every new minute and run the alert policy."""
    with state.lock:
        buf = state.buffers.setdefault(req.series_id, SeriesBuffer(state.settings.ingest_buffer_minutes))
        out = buf.add(req.samples)
        state.collectors.ingested.inc(out.accepted)
        preds: list = []
        if out.accepted and len(buf) >= p.required_history:
            preds = p.predict_recent(buf.frame(), out.accepted)
        decisions = apply_policy(state, p, req.series_id, preds)
        return IngestResponse(
            series_id=req.series_id, accepted=out.accepted, rejected=out.rejected,
            gap_resets=out.gap_resets, buffered=len(buf), required_history=p.required_history,
            prediction=preds[-1] if preds else None, scored_samples=len(preds),
            decision=decisions[-1] if decisions else None)


def _status(sid: str, state: ApiState) -> SeriesStatus:
    buf = state.buffers[sid]
    fr = buf.frame()
    return SeriesStatus(series_id=sid, buffered=len(buf),
                        last_timestamp=fr["timestamp"].iloc[-1].to_pydatetime() if len(buf) else None,
                        prediction=state.latest.get(sid),  # type: ignore[arg-type]
                        policy_level=state.policy.snapshot().get(sid, {}).get("level", "none"))


@router.get("/series", response_model=list[SeriesStatus])
def list_series(state: ApiState = Depends(get_state)) -> list[SeriesStatus]:
    with state.lock:
        return [_status(s, state) for s in sorted(state.buffers)]


@router.get("/series/{series_id}", response_model=SeriesStatus)
def get_series(series_id: str, state: ApiState = Depends(get_state)) -> SeriesStatus:
    with state.lock:
        if series_id not in state.buffers:
            raise HTTPException(status_code=404, detail=f"unknown series {series_id!r}")
        return _status(series_id, state)
