from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_predictor, get_state, require_api_key
from app.api.ingestion import ingest_points
from app.api.schemas import IngestRequest, IngestResponse, PredictRequest, SeriesStatus
from app.api.state import ApiState
from app.models.predictor import Predictor
from app.models.schemas import PredictionResult, points_to_frame

router = APIRouter(prefix="/v1", tags=["prediction"])


@router.post("/predict", response_model=PredictionResult)
def predict(req: PredictRequest, p: Predictor = Depends(get_predictor),
            state: ApiState = Depends(get_state)) -> PredictionResult:
    """Score the latest sample of one service from a window of recent 1-minute samples."""
    result = p.predict(points_to_frame(req.samples))
    state.collectors.predictions.labels(source="predict").inc()
    return result


@router.post("/metrics/ingest", response_model=IngestResponse, dependencies=[Depends(require_api_key)])
def ingest(req: IngestRequest, p: Predictor = Depends(get_predictor),
           state: ApiState = Depends(get_state)) -> IngestResponse:
    """Append samples to a series buffer, score every new minute and run the alert policy."""
    return ingest_points(state, p, req.series_id, req.samples)


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
