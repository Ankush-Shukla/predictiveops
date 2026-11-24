import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import Settings
from app.models.predictor import Predictor
from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("demo_walkthrough", ROOT / "scripts" / "demo_walkthrough.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture(scope="module")
def predictor(tmp_path_factory):
    df = generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))
    res = run_training(df, TrainConfig(model_type="logreg", model_root=tmp_path_factory.mktemp("m"),
                                       use_mlflow=False, max_fpr_for_threshold=0.2,
                                       gates=GateConfig(min_f1=0.0)))
    return Predictor(res.bundle, res.version)


def test_walkthrough_runs_every_scenario_in_process(predictor, tmp_path):
    s = Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "a.sqlite")
    lines: list[str] = []
    with TestClient(create_app(s, predictor, load_model=False)) as c:
        summary = demo.run_walkthrough(c, out=lines.append)
    text = "\n".join(lines)
    for header in ("== 1.", "== 2.", "== 3.", "== 4.", "== 5.", "== 6.", "== 7.", "Limitations"):
        assert header in text
    assert summary["live"]["accepted"] == 360 and summary["live"]["scored"] > 0
    assert summary["policy_levels"][:5] == ["none", "warn", "warn", "alert", "alert"]
    assert summary["canary"]["bad"] in ("rollback", "promote", "hold")
    assert "SYNTHETIC" in text and "Synthetic data only" in text


def test_main_reports_unreachable_api_cleanly(capsys):
    assert demo.main(["--base-url", "http://127.0.0.1:9"]) == 1
    assert "walkthrough failed" in capsys.readouterr().err
