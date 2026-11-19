"""Environment-based configuration (12-factor). Secrets are never hardcoded."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from PREDICTIVEOPS_* environment variables / .env."""

    model_config = SettingsConfigDict(
        env_prefix="PREDICTIVEOPS_", env_file=".env", extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True

    data_dir: Path = Path("data/generated")
    sqlite_path: Path = Path("data/generated/predictiveops.sqlite")
    prometheus_url: str = "http://localhost:9090"
    mlflow_tracking_uri: str = "file:./mlruns"

    # Prediction horizon: the synopsis specifies 15-30 minutes.
    horizon_minutes: int = Field(default=20, ge=15, le=30)
    synthetic_seed: int = 42

    # Remediation safety defaults (used from Phase 6). Dry-run is ON by default.
    dry_run: bool = True
    remediation_cooldown_seconds: int = Field(default=900, ge=0)
    max_actions_per_hour: int = Field(default=4, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
