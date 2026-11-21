import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app.models import registry
from app.models.bundle import ModelBundle, ModelNotFoundError, ModelSchemaMismatchError
from app.models.predictor import Predictor
from data.synthetic.generator import GeneratorConfig, generate_dataset, write_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

ROOT = Path(__file__).resolve().parents[2]
GEN = GeneratorConfig(days=8, n_series=3, seed=21)


@pytest.fixture(scope="module")
def df():
    return generate_dataset(GEN)


@pytest.fixture(scope="module")
def trained(df, tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    cfg = TrainConfig(model_type="hgb", model_root=root, use_mlflow=True, promote=True,
                      tracking_uri=f"sqlite:///{root}/mlflow.db", experiment="t",
                      gates=GateConfig(min_f1=0.3, max_fpr=0.2, must_beat_baseline_pr_auc=False),
                      max_fpr_for_threshold=0.2)
    return run_training(df, cfg), root


def test_report_contents_and_honest_split(trained):
    res, _ = trained
    r = res.report
    assert r["split"]["purge_gap_minutes"] == 20
    assert r["split"]["train_rows"] > r["split"]["val_rows"] > 0 and r["split"]["test_rows"] > 0
    for k in ("f1", "fpr", "pr_auc", "brier", "ece", "precision", "recall"):
        assert k in r["test"]
    assert "baseline_default" in r and "baseline_tuned" in r and "event" in r
    assert len(r["top_features_permutation"]) == 15
    assert res.bundle.metadata["data_source"] == "synthetic"
    assert "not evidence of real-world" in res.bundle.metadata["disclaimer"]


def test_model_beats_prevalence_brier_and_baseline_floor(trained):
    t = trained[0].report["test"]
    assert t["brier"] < t["brier_baseline_prevalence"]
    assert t["pr_auc"] > 2 * t["positive_rate"]  # far better than chance


def test_mlflow_run_and_registry(trained):
    res, root = trained
    assert res.mlflow and res.mlflow["run_id"] and res.mlflow["registered_version"] == "1"
    import mlflow
    from mlflow.tracking import MlflowClient

    mlflow.set_tracking_uri(f"sqlite:///{root}/mlflow.db")
    c = MlflowClient()
    run = c.get_run(res.mlflow["run_id"])
    assert "test_f1" in run.data.metrics and run.data.params["model_type"] == "hgb"
    mv = c.get_model_version_by_alias("predictiveops-failure-model", "champion")
    assert str(mv.version) == "1"


def test_champion_pointer_and_reload(trained):
    res, root = trained
    assert res.promoted and registry.champion_version(root) == res.version
    assert res.version in registry.list_versions(root)
    v, b = registry.load_champion(root)
    assert v == res.version and b.threshold == res.bundle.threshold


def test_predictor_matches_batch_scores(trained, df):
    res, root = trained
    p = Predictor.from_champion(root)
    g = df[df.series_id == "svc-00"].reset_index(drop=True)
    batch = p.predict_proba_frame(p._builder.transform(g).dropna())
    for t in (200, 1000, 5000):
        r = p.predict(g.iloc[: t + 1])
        assert np.isclose(r.probability, batch.loc[t], atol=1e-9)
        assert r.model_version == res.version and r.data_source == "synthetic"
        assert r.predicted_failure == (r.probability >= r.threshold)


def test_predictor_is_reproducible_after_reload(trained, df):
    _, root = trained
    g = df[df.series_id == "svc-01"].iloc[:900]
    a, b = Predictor.from_champion(root).predict(g), Predictor.from_champion(root).predict(g)
    assert a.probability == b.probability


def test_schema_mismatch_is_refused(trained, tmp_path):
    res, root = trained
    b = ModelBundle.load(root / res.version)
    b.schema = b.schema.model_copy(update={"schema_hash": "deadbeef"})
    with pytest.raises(ModelSchemaMismatchError):
        b.feature_builder()


def test_registry_errors(tmp_path):
    with pytest.raises(ModelNotFoundError):
        registry.load_champion(tmp_path)
    with pytest.raises(ModelNotFoundError):
        registry.set_champion(tmp_path, "nope")


def test_failed_gates_block_promotion(df, tmp_path):
    cfg = TrainConfig(model_type="logreg", model_root=tmp_path, use_mlflow=False, promote=True,
                      gates=GateConfig(min_f1=1.01), max_fpr_for_threshold=0.2)
    res = run_training(df, cfg)
    assert not res.report["gates"]["passed"] and not res.promoted
    assert registry.champion_version(tmp_path) is None
    assert res.version in registry.list_versions(tmp_path)  # saved but not champion


def test_xgb_trains(df, tmp_path):
    res = run_training(df, TrainConfig(model_type="xgb", model_root=tmp_path, use_mlflow=False,
                                       max_fpr_for_threshold=0.2))
    assert res.report["test"]["pr_auc"] > 0.2


def test_train_cli_enforces_gates(df, tmp_path):
    data = write_dataset(df, tmp_path / "d.csv.gz", GEN)
    base = [sys.executable, "scripts/train.py", "--data", str(data), "--model-type", "logreg",
            "--model-root", str(tmp_path / "m"), "--no-mlflow", "--enforce-gates"]
    bad = subprocess.run(base + ["--min-f1", "1.01"], cwd=ROOT, capture_output=True, text=True)
    assert bad.returncode == 2
    out = json.loads(bad.stdout.split("\nNOTE")[0])
    assert out["gates"]["passed"] is False and "SYNTHETIC" in bad.stdout
