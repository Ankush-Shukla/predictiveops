"""Request/response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.schemas import MetricPoint, PredictionResult
from app.policies.models import PolicyDecision
from app.services.canary_models import CanaryAssessment, CanaryConfig

FaultChoice = Literal["none", "memory_leak", "latency_creep", "error_ramp", "cpu_contention",
                      "disk_saturation"]


class PredictRequest(BaseModel):
    samples: list[MetricPoint] = Field(min_length=1, max_length=1440,
                                       description="Recent 1-minute samples of ONE service")


class IngestRequest(BaseModel):
    series_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    samples: list[MetricPoint] = Field(min_length=1, max_length=1440)


class IngestResponse(BaseModel):
    series_id: str
    accepted: int
    rejected: int
    gap_resets: int
    buffered: int
    required_history: int
    prediction: PredictionResult | None = None
    scored_samples: int = 0
    decision: PolicyDecision | None = Field(
        default=None, description="Policy decision after the newest sample")


class SeriesStatus(BaseModel):
    series_id: str
    buffered: int
    last_timestamp: datetime | None
    prediction: PredictionResult | None
    policy_level: str = "none"


class PolicyPreviewRequest(BaseModel):
    probabilities: list[float] = Field(min_length=1, max_length=1440,
                                       description="One calibrated probability per consecutive minute")
    threshold: float = Field(default=0.2, gt=0, lt=1)
    series_id: str = Field(default="preview", pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class SimulateRequest(BaseModel):
    fault_type: FaultChoice = "memory_leak"
    minutes: int = Field(default=360, ge=120, le=1440)
    fault_start: int = Field(default=150, ge=60, le=1300)
    ramp_minutes: int = Field(default=60, ge=10, le=180)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    spikes: bool = True


class SimulateSummary(BaseModel):
    failure_start_minute: int | None
    first_alert_minute: int | None
    lead_time_minutes: int | None = Field(
        description="Minutes between the first alert within the horizon and the failure onset")
    false_alert_minutes: int
    alert_minutes: int


class PolicyReplaySummary(BaseModel):
    sustain_alert: int
    sustain_remediate: int
    alert_minutes: int
    false_alert_minutes: int
    first_alert_minute: int | None
    lead_time_minutes: int | None
    would_remediate_minute: int | None = Field(description="First minute a (dry-run) remediation is planned")
    would_remediate_lead_minutes: int | None


class SimulateResponse(BaseModel):
    data_source: Literal["synthetic"] = "synthetic"
    model_version: str
    threshold: float
    horizon_minutes: int
    start: datetime
    minutes: int
    metrics: dict[str, list[float]]
    probability: list[float | None]
    is_unhealthy: list[bool]
    label_failure_within_h: list[int]
    summary: SimulateSummary
    policy_level: list[str | None]
    policy_summary: PolicyReplaySummary


SERVICE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class CanaryEvaluateRequest(BaseModel):
    service: str = Field(pattern=SERVICE_PATTERN,
                         description="Service name; selects the configured canary target (never a container)")
    baseline: list[MetricPoint] | None = Field(default=None, max_length=1440,
                                               description="Recent 1-minute samples of the stable version")
    candidate: list[MetricPoint] = Field(min_length=1, max_length=1440,
                                         description="Recent 1-minute samples of the canary version")


class RollbackInfo(BaseModel):
    outcome: str
    detail: str
    container: str | None


class CanaryResult(BaseModel):
    id: int | None = None
    service: str
    model_version: str
    evaluated_at: str
    assessment: CanaryAssessment
    rollback: RollbackInfo | None = None
    config: CanaryConfig


class CanarySimulateRequest(BaseModel):
    candidate_fault: FaultChoice = "memory_leak"
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    minutes: int = Field(default=180, ge=90, le=480)
    fault_start: int = Field(default=10, ge=5, le=400)
    ramp_minutes: int = Field(default=45, ge=10, le=180)


class CanaryCheckpoint(BaseModel):
    minute: int
    decision: str
    observed_minutes: int
    candidate_alert_fraction: float | None
    baseline_alert_fraction: float | None
    candidate_mean: float | None
    baseline_mean: float | None


class CanaryFinal(BaseModel):
    decision: str
    minute: int | None
    observed_minutes: int
    reasons: list[str]
    rollback_outcome: str | None = None
    rollback_container: str | None = None


class CanarySimulateResponse(BaseModel):
    data_source: Literal["synthetic"] = "synthetic"
    model_version: str
    threshold: float
    candidate_fault: str
    minutes: int
    baseline_probability: list[float | None]
    candidate_probability: list[float | None]
    candidate_unhealthy: list[bool]
    checkpoints: list[CanaryCheckpoint]
    final: CanaryFinal
    config: CanaryConfig
