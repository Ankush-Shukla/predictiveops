"""MLflow experiment tracking and model-registry integration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import mlflow
import mlflow.pyfunc
from mlflow.tracking import MlflowClient

from app.core.logging import get_logger
from training.pyfunc import PredictiveOpsPyfunc

log = get_logger(__name__)
CHAMPION_ALIAS = "champion"


def _flat_scalars(prefix: str, d: dict) -> dict[str, float]:
    out = {}
    for k, v in d.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and v == v:  # drop NaN
            out[f"{prefix}{k}"] = float(v)
    return out


def log_training_run(
    *, tracking_uri: str, experiment: str, run_name: str, params: dict, report: dict,
    bundle_dir: Path, register_as: str | None
) -> dict:
    """Log params/metrics/artifacts; optionally create a registered-model version.

    Returns {"run_id", "registered_version" (or None)}.
    """
    mlflow.set_tracking_uri(tracking_uri)
    if mlflow.get_experiment_by_name(experiment) is None:
        # keep run artifacts next to the tracking DB instead of ./mlruns in the repo root
        loc = None
        if tracking_uri.startswith("sqlite:///"):
            loc = (Path(tracking_uri.removeprefix("sqlite:///")).resolve().parent
                   / "mlartifacts" / experiment).as_uri()
        mlflow.create_experiment(experiment, artifact_location=loc)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({k: str(v) for k, v in params.items()})
        mlflow.set_tags({"data_source": str(params.get("data_source", "unknown")),
                         "gates_passed": str(report["gates"]["passed"])})
        for split in ("val", "test"):
            mlflow.log_metrics(_flat_scalars(f"{split}_", report[split]))
        mlflow.log_metrics(_flat_scalars("event_", report["event"]))
        mlflow.log_metrics(_flat_scalars("baseline_default_", report["baseline_default"]))
        mlflow.log_metrics(_flat_scalars("baseline_tuned_", report["baseline_tuned"]))
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "evaluation_report.json"
            rp.write_text(json.dumps(report, indent=2, default=str))
            mlflow.log_artifact(str(rp))
        mlflow.log_artifacts(str(bundle_dir), artifact_path="bundle")
        version = None
        try:
            info = mlflow.pyfunc.log_model(
                name="model", python_model=PredictiveOpsPyfunc(),
                artifacts={"bundle": str(bundle_dir)}, code_paths=["app", "training", "data"])
            if register_as:
                mv = mlflow.register_model(info.model_uri, register_as)
                version = str(mv.version)
        except Exception as exc:  # tracking/registry problems must never break training
            log.warning("mlflow model logging/registration failed", extra={"error": str(exc)})
        return {"run_id": run.info.run_id, "registered_version": version}


def set_champion_alias(tracking_uri: str, name: str, version: str) -> None:
    mlflow.set_tracking_uri(tracking_uri)
    MlflowClient().set_registered_model_alias(name, CHAMPION_ALIAS, version)
