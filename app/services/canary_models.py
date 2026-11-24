"""Canary configuration and result models (pure pydantic; no heavy imports)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Decision = Literal["promote", "hold", "rollback"]


class CanaryConfig(BaseModel):
    """All thresholds are explicit and configurable (env var PREDICTIVEOPS_CANARY as JSON)."""

    min_observation_minutes: int = Field(default=20, ge=5, le=720,
                                         description="Scored minutes required before ANY promote/rollback")
    max_observation_minutes: int = Field(default=120, ge=5, le=1440,
                                         description="Past this, an inconclusive canary asks for a human")
    rollback_alert_fraction: float = Field(default=0.30, gt=0, le=1)
    rollback_mean_margin: float = Field(default=0.10, ge=0, le=1,
                                        description="Candidate mean risk must exceed baseline's by this")
    promote_alert_fraction: float = Field(default=0.05, ge=0, le=1)
    promote_mean_margin: float = Field(default=0.05, ge=0, le=1)
    hard_fail_minutes: int = Field(default=5, ge=1, le=120,
                                   description="Consecutive minutes at/above hard_fail_probability")
    hard_fail_probability: float = Field(default=0.80, gt=0, le=1)
    require_baseline: bool = True

    @model_validator(mode="after")
    def _check(self) -> CanaryConfig:
        if self.promote_alert_fraction >= self.rollback_alert_fraction:
            raise ValueError("promote_alert_fraction must be < rollback_alert_fraction")
        if self.max_observation_minutes < self.min_observation_minutes:
            raise ValueError("max_observation_minutes must be >= min_observation_minutes")
        return self


class SideStats(BaseModel):
    alert_fraction: float
    mean_probability: float
    p95_probability: float
    longest_high_run: int


class CanaryAssessment(BaseModel):
    decision: Decision
    reasons: list[str]
    observed_minutes: int
    threshold: float
    candidate: SideStats | None = None
    baseline: SideStats | None = None
