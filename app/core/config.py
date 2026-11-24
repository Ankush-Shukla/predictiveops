"""Environment-based configuration (12-factor). Secrets are never hardcoded."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.policies.models import PolicyConfig, RemediationTarget
from app.services.canary_models import CanaryConfig


class Settings(BaseSettings):
    """Application settings, read from PREDICTIVEOPS_* environment variables / .env."""

    model_config = SettingsConfigDict(
        env_prefix="PREDICTIVEOPS_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True

    data_dir: Path = Path("data/generated")
    sqlite_path: Path = Path("data/generated/predictiveops.sqlite")
    prometheus_url: str = "http://localhost:9090"
    prometheus_enabled: bool = False  # poll Prometheus for live series
    prometheus_services: str = ""  # legacy combined-model services; development only
    prometheus_host_series_id: str = "home-server"
    prometheus_application_services: str = ""  # comma-separated stable service labels
    prometheus_step_seconds: float = Field(default=60.0, ge=1, le=3600)  # one model "minute" per step
    prometheus_poll_seconds: float = Field(default=10.0, ge=1, le=3600)
    prometheus_lookback_steps: int = Field(default=90, ge=30, le=1440)
    mlflow_tracking_uri: str = "sqlite:///artifacts/mlflow.db"
    mlflow_experiment: str = "predictiveops"
    mlflow_model_name: str = "predictiveops-failure-model"
    model_dir: Path = Path("artifacts/models")

    # Prediction horizon: the synopsis specifies 15-30 minutes.
    horizon_minutes: int = Field(default=20, ge=15, le=30)
    synthetic_seed: int = 42

    # API
    api_key: SecretStr | None = None  # if set, write endpoints require header X-API-Key
    ingest_buffer_minutes: int = Field(default=360, ge=60, le=10080)

    # CI/CD model quality gates (provisional; see PROJECT_STATUS.md).
    gate_min_f1: float = Field(default=0.70, ge=0, le=1)
    gate_max_fpr: float = Field(default=0.05, ge=0, le=1)

    # Alert policy and remediation safety. Remediation is OFF and dry-run is ON by default.
    policy_sustain_alert: int = Field(default=3, ge=1, le=60)
    policy_sustain_remediate: int = Field(default=5, ge=1, le=120)
    policy_remediate_threshold: float = Field(default=0.80, ge=0, le=1)
    remediation_enabled: bool = False
    remediation_targets: dict[str, RemediationTarget] = Field(default_factory=dict)  # JSON in env
    canary: CanaryConfig = Field(default_factory=CanaryConfig)  # JSON in env
    canary_targets: dict[str, RemediationTarget] = Field(default_factory=dict)  # service -> canary container
    dry_run: bool = True
    remediation_cooldown_seconds: int = Field(default=900, ge=0)
    max_actions_per_hour: int = Field(default=4, ge=0)


    @property
    def prometheus_service_list(self) -> list[str]:
        return [x.strip() for x in self.prometheus_services.split(",") if x.strip()]

    @property
    def prometheus_application_service_list(self) -> list[str]:
        return [x.strip() for x in self.prometheus_application_services.split(",") if x.strip()]

    def policy_config(self) -> PolicyConfig:
        return PolicyConfig(
            sustain_alert=self.policy_sustain_alert, sustain_remediate=self.policy_sustain_remediate,
            remediate_threshold=self.policy_remediate_threshold, remediation_enabled=self.remediation_enabled,
            dry_run=self.dry_run, cooldown_seconds=self.remediation_cooldown_seconds,
            max_actions_per_hour=self.max_actions_per_hour, targets=self.remediation_targets)


@lru_cache
def get_settings() -> Settings:
    return Settings()
