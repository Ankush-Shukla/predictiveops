from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_predictor, get_state, require_api_key
from app.api.schemas import (
    CanaryCheckpoint,
    CanaryEvaluateRequest,
    CanaryFinal,
    CanaryResult,
    CanarySimulateRequest,
    CanarySimulateResponse,
)
from app.api.state import ApiState
from app.models.predictor import Predictor
from app.models.schemas import points_to_frame
from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.policies.models import RemediationTarget
from app.services.canary import CanaryEvaluator, CanaryService
from app.services.canary_sim import simulate_canary

router = APIRouter(prefix="/v1/canary", tags=["canary"])


@router.post("/evaluate", response_model=CanaryResult, dependencies=[Depends(require_api_key)])
def evaluate(req: CanaryEvaluateRequest, p: Predictor = Depends(get_predictor),
             state: ApiState = Depends(get_state)) -> dict:
    """Judge a candidate deployment against a baseline with the predictive model.

    On a rollback decision the rollback is requested through the allowlisted, gated, audited action
    path for the service's CONFIGURED canary target. The request can never name a container.
    """
    svc = CanaryService(CanaryEvaluator(p, state.settings.canary), state.policy, state.store,
                        state.settings.canary_targets)
    base = points_to_frame(req.baseline) if req.baseline else None
    result = svc.run(req.service, base, points_to_frame(req.candidate))
    state.collectors.canary.labels(decision=result["assessment"]["decision"]).inc()
    if result["rollback"]:
        state.collectors.actions.labels(outcome=result["rollback"]["outcome"]).inc()
    return result


@router.get("", response_model=list[dict])
def list_runs(limit: int = Query(20, ge=1, le=200), state: ApiState = Depends(get_state)) -> list[dict]:
    return state.store.list_canary_runs(limit)


@router.get("/{run_id}", response_model=CanaryResult)
def get_run(run_id: int, state: ApiState = Depends(get_state)) -> dict:
    row = state.store.get_canary_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown canary run {run_id}")
    return row["result"]


@router.post("/simulate", response_model=CanarySimulateResponse)
def simulate(req: CanarySimulateRequest, p: Predictor = Depends(get_predictor),
             state: ApiState = Depends(get_state)) -> CanarySimulateResponse:
    """DEMO (SYNTHETIC data): a healthy baseline vs. a candidate release, checked every 5 minutes.

    Stops at the first promote/rollback. A rollback is only *simulated*: a throw-away engine with
    in-memory audit and forced dry-run records what would happen; the real audit log is untouched.
    """
    cfg = state.settings.canary
    run = simulate_canary(p, cfg, req.candidate_fault, seed=req.seed, minutes=req.minutes,
                          fault_start=req.fault_start, ramp_minutes=req.ramp_minutes)
    checkpoints = [CanaryCheckpoint(
        minute=m, decision=a.decision, observed_minutes=a.observed_minutes,
        candidate_alert_fraction=a.candidate.alert_fraction if a.candidate else None,
        baseline_alert_fraction=a.baseline.alert_fraction if a.baseline else None,
        candidate_mean=a.candidate.mean_probability if a.candidate else None,
        baseline_mean=a.baseline.mean_probability if a.baseline else None) for m, a in run.checkpoints]
    if run.final is not None:
        final = CanaryFinal(decision=run.final.decision, minute=run.final_minute,
                            observed_minutes=run.final.observed_minutes, reasons=run.final.reasons)
    else:
        final = CanaryFinal(decision="hold", minute=None, observed_minutes=0,
                            reasons=["no decision reached within the simulated window"])
    if final.decision == "rollback":
        demo = RemediationTarget(container="demo-canary", action="rollback_canary")
        cfg_demo = state.policy.config.model_copy(update={"dry_run": True, "remediation_enabled": True})
        engine = PolicyEngine(cfg_demo, AuditStore(":memory:"))
        when = run.candidate["timestamp"].iloc[final.minute or 0].to_pydatetime()
        outcome, _ = engine.request_action(
            series_id="demo-canary", target=demo, as_of=when, probability=None,
            model_version=p.version, reason="simulated canary rollback")
        final.rollback_outcome, final.rollback_container = outcome, demo.container
    state.collectors.canary.labels(decision=final.decision).inc()

    def series(s):
        return [None] * run.first_scored + [round(float(v), 5) for v in s]
    return CanarySimulateResponse(
        model_version=p.version, threshold=p.bundle.threshold, candidate_fault=req.candidate_fault,
        minutes=req.minutes, baseline_probability=series(run.baseline_scores),
        candidate_probability=series(run.candidate_scores),
        candidate_unhealthy=[bool(v) for v in run.candidate["is_unhealthy"]],
        checkpoints=checkpoints, final=final, config=cfg)
