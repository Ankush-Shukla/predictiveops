"""Policy engine: probability stream -> alert levels -> constrained remediation.

Levels (per series, evaluated once per scored minute):
  none       probability below the model threshold
  warn       at/above threshold for fewer than ``sustain_alert`` consecutive minutes
  alert      at/above threshold for >= sustain_alert consecutive minutes
  remediate  at/above max(remediate_threshold, threshold) for >= sustain_remediate minutes

Remediation passes ordered safety gates; every attempt is audited (once per distinct outcome
while the condition persists). Cooldowns and rate limits are read from the audit store, so they
survive restarts, and dry-run attempts count against them exactly like real ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.policies.actions import ALLOWED_ACTIONS, ActionExecutor, NoopExecutor
from app.policies.audit import AuditStore
from app.policies.models import LEVEL_RANK, Level, PolicyConfig, PolicyDecision

log = get_logger(__name__)


@dataclass
class _SeriesState:
    above: int = 0
    above_rem: int = 0
    level: Level = "none"
    last_audit_key: tuple | None = None


class PolicyEngine:
    def __init__(self, config: PolicyConfig, store: AuditStore | None = None,
                 executor: ActionExecutor | None = None) -> None:
        self.config = config
        self.store = store or AuditStore(":memory:")
        self.executor: ActionExecutor = executor or NoopExecutor()
        self._state: dict[str, _SeriesState] = {}

    # ---- introspection ----
    def snapshot(self) -> dict[str, dict]:
        return {s: {"level": st.level, "consecutive_above": st.above} for s, st in self._state.items()}

    # ---- core ----
    def evaluate(self, series_id: str, probability: float, threshold: float, as_of: datetime,
                 model_version: str | None = None) -> PolicyDecision:
        cfg, st = self.config, self._state.setdefault(series_id, _SeriesState())
        rem_thr = max(cfg.remediate_threshold, threshold)
        st.above = st.above + 1 if probability >= threshold else 0
        st.above_rem = st.above_rem + 1 if probability >= rem_thr else 0

        level: Level = "none"
        if st.above >= 1:
            level = "warn"
        if st.above >= cfg.sustain_alert:
            level = "alert"
        if st.above_rem >= cfg.sustain_remediate:
            level = "remediate"

        prev = st.level
        reasons: list[str] = []
        if level != "none":
            reasons.append(f"p={probability:.3f} >= {threshold:.3f} for {st.above} min")
        if level == "warn":
            reasons.append(f"not yet sustained (need {cfg.sustain_alert} min)")
        dec = PolicyDecision(series_id=series_id, as_of=as_of, level=level, probability=probability,
                             threshold=threshold, consecutive_above=st.above, reasons=reasons)

        if LEVEL_RANK[level] > LEVEL_RANK[prev] and level in ("alert", "remediate"):
            self.store.add_alert(ts=as_of, series_id=series_id, level=level, probability=probability,
                                 threshold=threshold, model_version=model_version,
                                 message=f"{level}: failure risk {probability:.0%} sustained {st.above} min")
        elif prev in ("alert", "remediate") and level == "none":
            self.store.add_alert(ts=as_of, series_id=series_id, level="resolved", probability=probability,
                                 threshold=threshold, model_version=model_version,
                                 message="risk back below threshold")
        st.level = level

        if level == "remediate":
            self._attempt(dec, st, model_version)
        else:
            st.last_audit_key = None
        return dec

    # ---- remediation gates ----
    def _attempt(self, dec: PolicyDecision, st: _SeriesState, model_version: str | None) -> None:
        cfg, now = self.config, dec.as_of
        target = cfg.targets.get(dec.series_id)
        action = target.action if target else None
        container = target.container if target else None
        outcome, reason = self._gate(target, now)

        if outcome is None:  # all gates passed
            if cfg.dry_run:
                outcome, reason = "dry_run", "dry-run: action NOT executed"
            else:
                try:
                    detail = self.executor.execute(action, container)  # type: ignore[arg-type]
                    outcome, reason = "executed", detail
                except Exception as exc:  # never let remediation errors break scoring
                    outcome, reason = "failed", f"{type(exc).__name__}: {exc}"
                    log.error("remediation failed", extra={"series_id": dec.series_id, "error": reason})

        dec.action, dec.target, dec.action_outcome = action, container, outcome  # type: ignore[assignment]
        dec.reasons.append(f"remediation {outcome}: {reason}")
        key = (action, container, outcome)
        if key != st.last_audit_key:  # one audit row per distinct outcome while condition persists
            st.last_audit_key = key
            self.store.add_audit(ts=now, series_id=dec.series_id, action=action or "none", target=container,
                                 outcome=outcome, dry_run=cfg.dry_run, probability=dec.probability,
                                 model_version=model_version, reason=reason,
                                 details={"consecutive_above": dec.consecutive_above})
        if outcome in ("executed", "dry_run"):  # require fresh evidence after an action
            st.above = st.above_rem = 0
            st.level = "none"
            st.last_audit_key = None

    def _gate(self, target, now: datetime) -> tuple[str | None, str]:
        cfg = self.config
        if target is None:
            return "blocked_no_target", "no remediation target configured for this series"
        if target.action not in ALLOWED_ACTIONS or target.action not in cfg.allowed_actions:
            return "blocked_not_allowlisted", f"action {target.action!r} is not allowlisted"
        if not cfg.remediation_enabled:
            return "blocked_disabled", "remediation is disabled (master switch off)"
        last = self.store.last_action_time(target.container)
        if last is not None and now - last < timedelta(seconds=cfg.cooldown_seconds):
            return "skipped_cooldown", f"cooldown {cfg.cooldown_seconds}s since {last.isoformat()}"
        if self.store.count_actions_since(now - timedelta(hours=1), now) >= cfg.max_actions_per_hour:
            return "skipped_rate_limit", f"max {cfg.max_actions_per_hour} actions/hour reached"
        return None, ""
