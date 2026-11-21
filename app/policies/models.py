"""Policy configuration and decision models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ActionName = Literal["restart_container", "rollback_canary"]
Level = Literal["none", "warn", "alert", "remediate"]
LEVEL_RANK: dict[str, int] = {"none": 0, "warn": 1, "alert": 2, "remediate": 3}
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class RemediationTarget(BaseModel):
    """Explicit mapping series -> ONE container and ONE allowlisted action."""

    container: str = Field(pattern=NAME_PATTERN)
    action: ActionName = "restart_container"


class PolicyConfig(BaseModel):
    sustain_alert: int = Field(default=3, ge=1, le=60,
                               description="Consecutive minutes at/above the model threshold to alert")
    sustain_remediate: int = Field(default=5, ge=1, le=120)
    remediate_threshold: float = Field(default=0.80, ge=0, le=1)
    remediation_enabled: bool = False  # master switch: OFF by default
    dry_run: bool = True  # even when enabled, nothing executes unless dry_run is False
    cooldown_seconds: int = Field(default=900, ge=0)
    max_actions_per_hour: int = Field(default=4, ge=0)
    allowed_actions: tuple[ActionName, ...] = ("restart_container", "rollback_canary")
    targets: dict[str, RemediationTarget] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> PolicyConfig:
        if self.sustain_remediate < self.sustain_alert:
            raise ValueError("sustain_remediate must be >= sustain_alert")
        return self


class PolicyDecision(BaseModel):
    series_id: str
    as_of: datetime
    level: Level
    probability: float
    threshold: float
    consecutive_above: int
    action: ActionName | None = None
    target: str | None = None
    action_outcome: str | None = None
    reasons: list[str] = Field(default_factory=list)
