from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Depends

from app.api.deps import get_predictor, get_state
from app.api.schemas import PolicyReplaySummary, SimulateRequest, SimulateResponse, SimulateSummary
from app.api.state import ApiState
from app.core.domain import METRIC_COLUMNS
from app.models.predictor import Predictor
from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.policies.models import RemediationTarget
from data.synthetic.scenarios import generate_scenario

router = APIRouter(prefix="/v1", tags=["demo"])


@router.post("/simulate", response_model=SimulateResponse)
def simulate(req: SimulateRequest, p: Predictor = Depends(get_predictor),
             state: ApiState = Depends(get_state)) -> SimulateResponse:
    """DEMO: generate a SYNTHETIC scenario and score every minute with the champion model.

    Scores use the exact same feature builder and calibrated model as /v1/predict.
    """
    horizon = int(p.bundle.metadata.get("horizon_minutes") or 20)
    df = generate_scenario(req.fault_type, minutes=req.minutes, fault_start=req.fault_start,
                           ramp_minutes=req.ramp_minutes, seed=req.seed, spikes=req.spikes,
                           horizon_minutes=horizon)
    feats = p._builder.transform(df)
    ok = feats.notna().all(axis=1)
    prob = np.full(len(df), np.nan)
    prob[ok.to_numpy()] = p.predict_proba_frame(feats[ok]).to_numpy()
    state.collectors.predictions.labels(source="simulate").inc(int(ok.sum()))

    thr = p.bundle.threshold
    u = df["is_unhealthy"].to_numpy()
    alert = np.nan_to_num(prob, nan=0.0) >= thr
    onset = int(np.argmax(u)) if u.any() else None
    first_any = int(np.argmax(alert)) if alert.any() else None
    lead, first_in_window = None, None
    if onset is not None:
        win = alert[max(onset - horizon, 0):onset]
        if win.any():
            first_in_window = max(onset - horizon, 0) + int(np.argmax(win))
            lead = onset - first_in_window
    false_alerts = int((alert & ~u & (df["label_failure_within_h"].to_numpy() == 0)).sum())
    # ---- alert-policy replay (throw-away engine, in-memory audit, forced dry-run, demo target) ----
    cfg = state.policy.config.model_copy(update={
        "dry_run": True, "remediation_enabled": True,
        "targets": {"demo": RemediationTarget(container="demo-app", action="restart_container")}})
    engine = PolicyEngine(cfg, AuditStore(":memory:"))
    levels: list[str | None] = []
    would_remediate = None
    for i, (ts, pv) in enumerate(zip(df["timestamp"], prob, strict=True)):
        if np.isnan(pv) or u[i]:
            levels.append(None)
            continue
        d = engine.evaluate("demo", float(pv), thr, ts.to_pydatetime())
        levels.append(d.level)
        if d.action_outcome == "dry_run" and would_remediate is None:
            would_remediate = i
    lv = np.array([x in ("alert", "remediate") for x in levels])
    lab = df["label_failure_within_h"].to_numpy()
    p_first = p_lead = None
    if onset is not None:
        lo = max(onset - horizon, 0)
        if lv[lo:onset].any():
            p_first = lo + int(np.argmax(lv[lo:onset]))
            p_lead = onset - p_first
    policy_summary = PolicyReplaySummary(
        sustain_alert=cfg.sustain_alert, sustain_remediate=cfg.sustain_remediate,
        alert_minutes=int(lv.sum()), false_alert_minutes=int((lv & ~u & (lab == 0)).sum()),
        first_alert_minute=p_first, lead_time_minutes=p_lead, would_remediate_minute=would_remediate,
        would_remediate_lead_minutes=(onset - would_remediate) if (onset is not None and
                                                                  would_remediate is not None
                                                                  and would_remediate < onset) else None)
    return SimulateResponse(
        model_version=p.version, threshold=thr, horizon_minutes=horizon,
        start=df["timestamp"].iloc[0].to_pydatetime(), minutes=len(df),
        metrics={c: [round(float(v), 4) for v in df[c]] for c in METRIC_COLUMNS},
        probability=[None if np.isnan(v) else round(float(v), 5) for v in prob],
        is_unhealthy=[bool(v) for v in u],
        label_failure_within_h=[int(v) for v in df["label_failure_within_h"]],
        summary=SimulateSummary(
            failure_start_minute=onset,
            first_alert_minute=first_in_window if first_in_window is not None else first_any,
            lead_time_minutes=lead, false_alert_minutes=false_alerts, alert_minutes=int(alert.sum())),
        policy_level=levels, policy_summary=policy_summary)
