"""Predictive canary validation: judge a candidate deployment against a baseline.

The SAME calibrated model scores both the baseline and the candidate. A candidate is judged on
the minutes where both have scores, so a shared problem (e.g. noisy host) is not blamed on it.

Decision rules (all thresholds in ``CanaryConfig``), evaluated in this order:
  hold      fewer than ``min_observation_minutes`` overlapping scored minutes, or no baseline
  rollback  candidate sat >= hard_fail_minutes at >= hard_fail_probability while the baseline was
            NOT also high, OR candidate alert fraction >= rollback_alert_fraction AND its mean
            risk exceeds the baseline's by >= rollback_mean_margin
  promote   candidate alert fraction <= promote_alert_fraction AND mean risk within
            promote_mean_margin of the baseline's AND no hard-fail run
  hold      everything else (inconclusive); never promotes on weak evidence
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.models.predictor import Predictor
from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.policies.models import RemediationTarget
from app.services.canary_models import CanaryAssessment, CanaryConfig, SideStats


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def _stats(p: np.ndarray, thr: float, hard: float) -> SideStats:
    return SideStats(alert_fraction=float((p >= thr).mean()), mean_probability=float(p.mean()),
                     p95_probability=float(np.quantile(p, 0.95)),
                     longest_high_run=_longest_run(p >= hard))


def assess(baseline: pd.Series | None, candidate: pd.Series, threshold: float,
           cfg: CanaryConfig) -> CanaryAssessment:
    """Pure decision function. Inputs are calibrated probabilities indexed by timestamp."""
    if baseline is None or len(baseline) == 0:
        if cfg.require_baseline:
            return CanaryAssessment(decision="hold", observed_minutes=0, threshold=threshold,
                                    reasons=["no baseline scores: cannot judge the candidate relative to it"])
        both = candidate
        base_p = None
    else:
        both = candidate[candidate.index.isin(baseline.index)]
        base_p = baseline.loc[both.index].to_numpy() if len(both) else None
    n = len(both)
    if n < cfg.min_observation_minutes:
        return CanaryAssessment(decision="hold", observed_minutes=n, threshold=threshold, reasons=[
            f"observed {n} of {cfg.min_observation_minutes} required overlapping minutes; keep observing"])

    c = both.to_numpy()
    cs = _stats(c, threshold, cfg.hard_fail_probability)
    bs = _stats(base_p, threshold, cfg.hard_fail_probability) if base_p is not None else None
    base_mean = bs.mean_probability if bs else 0.0
    diff = cs.mean_probability - base_mean
    reasons = [f"candidate alert fraction {cs.alert_fraction:.0%}, mean risk {cs.mean_probability:.1%}"
               + (f" vs baseline {bs.alert_fraction:.0%}, {bs.mean_probability:.1%}" if bs else "")]

    hard = False
    if cs.longest_high_run >= cfg.hard_fail_minutes:
        high = c >= cfg.hard_fail_probability
        base_during = float(base_p[high].mean()) if base_p is not None else 0.0
        hard = base_during < threshold  # baseline not simultaneously high
        if not hard:
            reasons.append("candidate hard-fail run coincides with a high-risk baseline: not attributable")

    if hard:
        decision = "rollback"
        reasons.append(f"candidate stayed >= {cfg.hard_fail_probability:.0%} risk for "
                       f"{cs.longest_high_run} consecutive minutes while the baseline did not")
    elif cs.alert_fraction >= cfg.rollback_alert_fraction and diff >= cfg.rollback_mean_margin:
        decision = "rollback"
        reasons.append(f"alert fraction >= {cfg.rollback_alert_fraction:.0%} and mean risk "
                       f"{diff:+.1%} above baseline (margin {cfg.rollback_mean_margin:.0%})")
    elif cs.alert_fraction <= cfg.promote_alert_fraction and diff <= cfg.promote_mean_margin:
        decision = "promote"
        reasons.append(f"alert fraction <= {cfg.promote_alert_fraction:.0%} and mean risk within "
                       f"{cfg.promote_mean_margin:.0%} of baseline")
    else:
        decision = "hold"
        if cs.alert_fraction >= cfg.rollback_alert_fraction:
            reasons.append("candidate is risky but the baseline is equally risky: not attributable")
        reasons.append("evidence is inconclusive")
        if n >= cfg.max_observation_minutes:
            reasons.append("observation limit reached: a human decision is required")
    return CanaryAssessment(decision=decision, reasons=reasons, observed_minutes=n, threshold=threshold,
                            candidate=cs, baseline=bs)


class CanaryEvaluator:
    def __init__(self, predictor: Predictor, config: CanaryConfig) -> None:
        self.predictor, self.config = predictor, config

    def score(self, window: pd.DataFrame) -> pd.Series:
        """Calibrated probabilities (indexed by timestamp) for every minute with complete features."""
        res = self.predictor.predict_recent(window, len(window))
        return pd.Series([r.probability for r in res],
                         index=pd.DatetimeIndex([pd.Timestamp(r.as_of) for r in res]), dtype=float)

    def evaluate(self, baseline: pd.DataFrame | None, candidate: pd.DataFrame) -> CanaryAssessment:
        base = self.score(baseline) if baseline is not None and len(baseline) else None
        return assess(base, self.score(candidate), self.predictor.bundle.threshold, self.config)


class CanaryService:
    """Evaluate, persist, and (only on rollback) request the allowlisted rollback action."""

    def __init__(self, evaluator: CanaryEvaluator, engine: PolicyEngine, store: AuditStore,
                 targets: dict[str, RemediationTarget]) -> None:
        self.evaluator, self.engine, self.store, self.targets = evaluator, engine, store, targets

    def run(self, service: str, baseline: pd.DataFrame | None, candidate: pd.DataFrame,
            *, persist: bool = True) -> dict:
        a = self.evaluator.evaluate(baseline, candidate)
        as_of = pd.Timestamp(candidate["timestamp"].max()).to_pydatetime()
        rollback = None
        if a.decision == "rollback":
            target = self.targets.get(service)
            if target is not None and target.action != "rollback_canary":
                outcome, detail = "blocked_not_allowlisted", "canary targets must use rollback_canary"
                self.engine.store.add_audit(
                    ts=as_of, series_id=service, action=target.action, target=target.container,
                    outcome=outcome, dry_run=self.engine.config.dry_run, probability=None,
                    model_version=self.evaluator.predictor.version, reason=detail)
            else:
                outcome, detail = self.engine.request_action(
                    series_id=service, target=target, as_of=as_of, probability=None,
                    model_version=self.evaluator.predictor.version,
                    reason="canary rollback: " + "; ".join(a.reasons[-1:]),
                    details={"observed_minutes": a.observed_minutes})
            rollback = {"outcome": outcome, "detail": detail,
                        "container": target.container if target else None}
        result = {"service": service, "model_version": self.evaluator.predictor.version,
                  "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "assessment": a.model_dump(), "rollback": rollback,
                  "config": self.evaluator.config.model_dump()}
        if persist:
            result["id"] = self.store.add_canary_run(ts=as_of, service=service, decision=a.decision,
                                                     result=result)
        return result
