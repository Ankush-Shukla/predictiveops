from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_state
from app.api.schemas import PolicyPreviewRequest
from app.api.state import ApiState
from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.policies.models import PolicyDecision

router = APIRouter(prefix="/v1", tags=["policy"])


@router.get("/policy")
def policy_info(state: ApiState = Depends(get_state)) -> dict:
    """Active alert policy, remediation safety switches, and live per-series state."""
    cfg = state.policy.config
    live = cfg.remediation_enabled and not cfg.dry_run
    return {
        "config": cfg.model_dump(),
        "executor": ("docker (label-verified, allowlisted actions only)" if live
                     else "none (dry-run or disabled)"),
        "series": state.policy.snapshot(),
        "notes": ["Remediation is disabled by default; when enabled, dry_run defaults to true.",
                  "Only allowlisted actions on explicitly configured, label-verified containers can run."],
    }


@router.post("/policy/evaluate", response_model=list[PolicyDecision])
def policy_preview(req: PolicyPreviewRequest, state: ApiState = Depends(get_state)) -> list[PolicyDecision]:
    """Preview what the policy would decide for a probability sequence.

    Side-effect free: uses a throw-away engine with an in-memory audit store and forced dry-run.
    """
    cfg = state.policy.config.model_copy(update={"dry_run": True})
    engine = PolicyEngine(cfg, AuditStore(":memory:"))
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    return [engine.evaluate(req.series_id, p, req.threshold, t0 + timedelta(minutes=i))
            for i, p in enumerate(req.probabilities)]


@router.get("/alerts")
def alerts(limit: int = Query(50, ge=1, le=500), series_id: str | None = None,
           state: ApiState = Depends(get_state)) -> list[dict]:
    return state.store.list_alerts(limit, series_id)


@router.get("/audit")
def audit(limit: int = Query(50, ge=1, le=500), series_id: str | None = None,
          state: ApiState = Depends(get_state)) -> list[dict]:
    """Every remediation decision (executed, dry-run, blocked, skipped, failed), newest first."""
    return state.store.list_audit(limit, series_id)
