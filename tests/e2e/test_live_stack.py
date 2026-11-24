"""End-to-end test on REAL processes (no Docker): node_exporter, demo target, Prometheus, API.

Skipped unless PROMETHEUS_BIN points at a Prometheus binary (NODE_EXPORTER_BIN is optional).
    PROMETHEUS_BIN=/path/prometheus NODE_EXPORTER_BIN=/path/node_exporter pytest -m e2e -o addopts=""
Takes ~3-4 minutes: the demo target runs accelerated (one simulated minute per 2 s).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

ROOT = Path(__file__).resolve().parents[2]
PROM = os.getenv("PROMETHEUS_BIN")
NODE = os.getenv("NODE_EXPORTER_BIN")
TOKEN = "e2e-token"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not PROM, reason="PROMETHEUS_BIN not set")]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, timeout, interval=1.0, what="condition"):
    end, last = time.time() + timeout, None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001 - polling helper
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stack")
    models = tmp / "models"
    df = generate_dataset(GeneratorConfig(days=14, n_series=4, seed=42))
    res = run_training(df, TrainConfig(model_type="hgb", model_root=models, use_mlflow=False, promote=True,
                                       gates=GateConfig(min_f1=0.5, max_fpr=0.1), max_fpr_for_threshold=0.05))
    assert res.promoted, res.report["gates"]

    ports = {k: free_port() for k in ("demo", "api", "prom", "node")}
    procs, logs = [], {}

    def spawn(name, cmd, env=None):
        logs[name] = open(tmp / f"{name}.log", "w")  # noqa: SIM115
        procs.append(subprocess.Popen(cmd, cwd=ROOT, stdout=logs[name], stderr=subprocess.STDOUT,
                                      env={**os.environ, "PYTHONPATH": str(ROOT), **(env or {})}))

    try:
        if NODE:
            spawn("node", [NODE, f"--web.listen-address=127.0.0.1:{ports['node']}"])
        spawn("demo", [sys.executable, "-m", "uvicorn", "demo_app.main:app_factory", "--factory",
                       "--port", str(ports["demo"]), "--log-level", "warning"],
              {"DEMO_SIM_MINUTE_SECONDS": "2", "DEMO_ADMIN_TOKEN": TOKEN})
        jobs = [
            {"job_name": "demo-app", "static_configs": [{"targets": [f"127.0.0.1:{ports['demo']}"],
                                                         "labels": {"service": "demo-app"}}]},
            {"job_name": "predictiveops-api", "static_configs": [{"targets": [f"127.0.0.1:{ports['api']}"]}]}]
        if NODE:
            jobs.append({"job_name": "node-exporter",
                         "static_configs": [{"targets": [f"127.0.0.1:{ports['node']}"]}]})
        rules = tmp / "alert_rules.yml"
        rules.write_text((ROOT / "monitoring" / "alert_rules.yml").read_text())
        (tmp / "prometheus.yml").write_text(yaml.safe_dump({
            "global": {"scrape_interval": "1s", "evaluation_interval": "2s"},
            "rule_files": [str(rules)], "scrape_configs": jobs}))
        spawn("prom", [PROM, f"--config.file={tmp / 'prometheus.yml'}", f"--storage.tsdb.path={tmp / 'tsdb'}",
                       f"--web.listen-address=127.0.0.1:{ports['prom']}"])
        spawn("api", [sys.executable, "-m", "uvicorn", "app.api.main:app_factory", "--factory",
                      "--port", str(ports["api"]), "--log-level", "warning"], {
            "PREDICTIVEOPS_MODEL_DIR": str(models), "PREDICTIVEOPS_SQLITE_PATH": str(tmp / "a.sqlite"),
            "PREDICTIVEOPS_PROMETHEUS_ENABLED": "true",
            "PREDICTIVEOPS_PROMETHEUS_URL": f"http://127.0.0.1:{ports['prom']}",
            "PREDICTIVEOPS_PROMETHEUS_SERVICES": "demo-app", "PREDICTIVEOPS_PROMETHEUS_STEP_SECONDS": "2",
            "PREDICTIVEOPS_PROMETHEUS_POLL_SECONDS": "1", "PREDICTIVEOPS_PROMETHEUS_LOOKBACK_STEPS": "120",
            "PREDICTIVEOPS_REMEDIATION_ENABLED": "true", "PREDICTIVEOPS_LOG_LEVEL": "WARNING",
            "PREDICTIVEOPS_REMEDIATION_TARGETS": json.dumps(
                {"demo-app": {"container": "predictiveops-demo-app", "action": "restart_container"}})})
        base = {k: f"http://127.0.0.1:{v}" for k, v in ports.items()}
        wait_for(lambda: httpx.get(base["api"] + "/ready").status_code == 200, 60, what="api ready")
        wait_for(lambda: httpx.get(base["prom"] + "/-/ready").status_code == 200, 60, what="prometheus ready")
        yield base
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in logs.values():
            f.close()


def prom_query(base, q):
    r = httpx.get(base["prom"] + "/api/v1/query", params={"query": q}).json()
    assert r["status"] == "success", (q, r)
    return r["data"]["result"]


def test_live_pipeline_detects_fault_and_policy_acts_in_dry_run(stack):
    api, demo = stack["api"], stack["demo"]
    series = wait_for(lambda: (lambda j: j if j["buffered"] >= 45 and j["prediction"] else None)(
        httpx.get(api + "/v1/series/demo-app").json()), 150, 2, "warm-up of the live series")
    assert series["prediction"]["data_source"] == "synthetic"
    assert series["prediction"]["probability"] < series["prediction"]["threshold"]  # healthy at start
    assert httpx.get(api + "/v1/sources").json()["prometheus"]["services"]["demo-app"]["error"] is None
    assert httpx.get(api + "/v1/audit").json() == []

    h = {"X-Admin-Token": TOKEN}
    body = {"fault_type": "memory_leak", "ramp_minutes": 30}
    assert httpx.post(demo + "/fault", json=body).status_code == 401
    assert httpx.post(demo + "/fault", json=body, headers=h).status_code == 200

    alerts = wait_for(lambda: (lambda a: a if {"alert"} <= {x["level"] for x in a} else None)(
        httpx.get(api + "/v1/alerts").json()), 150, 2, "policy alert after fault injection")
    assert alerts[-1]["series_id"] == "demo-app"
    audit = wait_for(lambda: httpx.get(api + "/v1/audit").json() or None, 120, 2, "remediation decision")
    assert audit[-1]["outcome"] == "dry_run" and audit[-1]["dry_run"] is True
    assert audit[-1]["target"] == "predictiveops-demo-app" and audit[-1]["action"] == "restart_container"

    # the API's own metrics are scraped by Prometheus and visible to Grafana
    level_q = 'predictiveops_policy_level{series_id="demo-app"}'
    wait_for(lambda: prom_query(stack, level_q), 30, 1, "api metrics scraped")
    peak = prom_query(stack, "max_over_time(predictiveops_failure_probability[5m])")
    assert float(peak[0]["value"][1]) > 0.5
    rules = httpx.get(stack["prom"] + "/api/v1/rules").json()["data"]["groups"]
    assert any(g["name"] == "predictiveops" for g in rules)
    httpx.post(demo + "/fault/clear", headers=h)


def test_every_grafana_dashboard_query_parses_and_core_series_exist(stack):
    dash = json.loads((ROOT / "grafana" / "dashboards" / "predictiveops.json").read_text())
    exprs = [t["expr"] for p in dash["panels"] for t in p["targets"]]
    assert len(exprs) >= 15
    for e in exprs:
        prom_query(stack, e)  # asserts status == success (valid PromQL, no evaluation error)
    for must in ("demo_cpu_pct", "demo_memory_pct", "predictiveops_failure_probability"):
        assert prom_query(stack, must), must
    if NODE:
        assert prom_query(stack, 'avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))')
