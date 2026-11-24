import time

import pytest
from fastapi.testclient import TestClient

from app.api.main import app_factory, create_app
from app.core.config import Settings
from app.core.exceptions import PredictiveOpsError
from app.policies import actions
from app.policies.actions import LazyDockerExecutor
from data.synthetic.generator import GeneratorConfig, generate_dataset
from demo_app.main import app_factory as demo_factory
from demo_app.main import create_demo_app
from training.gates import GateConfig
from training.train import TrainConfig, run_training


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    df = generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))
    run_training(df, TrainConfig(model_type="logreg", model_root=root, use_mlflow=False, promote=True,
                                 max_fpr_for_threshold=0.2, gates=GateConfig(min_f1=0.0)))
    return root


def test_startup_loads_champion_from_model_dir(model_dir, tmp_path):
    s = Settings(_env_file=None, log_level="ERROR", model_dir=model_dir, sqlite_path=tmp_path / "a.sqlite")
    with TestClient(create_app(s)) as c:
        assert c.get("/ready").status_code == 200
        assert c.get("/v1/model").json()["data_source"] == "synthetic"


def test_startup_without_model_stays_alive_but_not_ready(tmp_path):
    s = Settings(_env_file=None, log_level="ERROR", model_dir=tmp_path / "none",
                 sqlite_path=tmp_path / "a.sqlite")
    with TestClient(create_app(s)) as c:
        assert c.get("/health").status_code == 200 and c.get("/ready").status_code == 503
        assert c.get("/").status_code == 200  # dashboard still served


def test_unexpected_domain_error_becomes_clean_500(tmp_path):
    app = create_app(Settings(_env_file=None, log_level="ERROR", sqlite_path=tmp_path / "a.sqlite"),
                     None, load_model=False)

    @app.get("/boom")
    def boom():
        raise PredictiveOpsError("kaput")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
        assert r.status_code == 500 and r.json() == {"error": "internal_error", "detail": "kaput"}


def test_lazy_docker_executor_connects_once_on_first_use(monkeypatch):
    calls = {"connect": 0, "exec": []}

    class Fake:
        def execute(self, action, container):
            calls["exec"].append((action, container))
            return "ok"

    def fake_from_env():
        calls["connect"] += 1
        return Fake()

    monkeypatch.setattr(actions.DockerExecutor, "from_env", staticmethod(fake_from_env))
    ex = LazyDockerExecutor()
    assert calls["connect"] == 0  # startup never touches Docker
    ex.execute("restart_container", "a")
    ex.execute("restart_container", "b")
    assert calls["connect"] == 1 and len(calls["exec"]) == 2


def test_app_factories_build_apps():
    assert app_factory().title == "PredictiveOps API"
    assert demo_factory().title == "PredictiveOps demo target"


def test_demo_app_background_clock_advances():
    app = create_demo_app(sim_minute_seconds=0.05, autostart=True, admin_token="t")
    with TestClient(app) as c:
        deadline = time.time() + 5
        ticks = 0.0
        while time.time() < deadline and ticks < 4:
            lines = c.get("/metrics").text.splitlines()
            ticks = float(next(x for x in lines if x.startswith("demo_sim_minutes_total ")).split()[1])
            time.sleep(0.05)
        assert ticks >= 4
