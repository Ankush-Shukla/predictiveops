"""Static invariants for the infrastructure files (these do NOT start Docker)."""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(rel):
    return yaml.safe_load((ROOT / rel).read_text())


COMPOSE = load("docker-compose.yml")
SVC = COMPOSE["services"]


def test_images_are_pinned_and_no_latest():
    for name, s in SVC.items():
        img = s.get("image", "")
        assert not img.endswith(":latest") and (":" in img or "build" in s), name
    for ext in ("prometheus", "node-exporter", "grafana"):
        assert re.search(r":v?\d+\.\d+", SVC[ext]["image"]), ext


def test_secrets_are_not_hardcoded_and_required_ones_have_no_default():
    assert ":?" in SVC["grafana"]["environment"]["GRAFANA_ADMIN_PASSWORD".replace("GRAFANA_", "GF_SECURITY_")]
    for svc, key in (("api", "PREDICTIVEOPS_API_KEY"), ("demo-app", "DEMO_ADMIN_TOKEN")):
        assert SVC[svc]["environment"][key].endswith(":-}")  # empty (disabled) by default
    excluded_dirs = {".git", ".venv", "venv", ".tox", ".nox", "node_modules"}
    text = "\n".join(
        p.read_text(errors="ignore")
        for p in ROOT.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".yml", ".yaml", ".json", ".md", ".txt", ".env"}
        and not any(part in excluded_dirs for part in p.parts)
        and "tests" not in p.parts
    )
    for pat in (r"ghp_[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}", r"BEGIN (RSA |EC )?PRIVATE KEY",
                r"sk-ant-[A-Za-z0-9_-]{20,}"):
        assert not re.search(pat, text), pat


def test_default_stack_is_safe_by_default():
    env = SVC["api"]["environment"]
    assert env["PREDICTIVEOPS_REMEDIATION_ENABLED"].endswith(":-false}")
    assert env["PREDICTIVEOPS_DRY_RUN"].endswith(":-true}")
    assert not any("docker.sock" in str(v) for s in SVC.values() for v in s.get("volumes", []))
    override = load("docker-compose.remediation.yml")["services"]["api"]
    assert any("docker.sock" in v for v in override["volumes"])
    assert override["environment"]["PREDICTIVEOPS_DRY_RUN"] == "false"  # only the opt-in file goes live


def test_labels_and_targets_line_up_with_safety_checks():
    assert SVC["demo-app"]["labels"]["predictiveops.managed"] == "true"
    can = SVC["demo-app-canary"]["labels"]
    assert can["predictiveops.managed"] == "true" and can["predictiveops.role"] == "canary"
    env = SVC["api"]["environment"]
    rem = json.loads(env["PREDICTIVEOPS_REMEDIATION_TARGETS"])["demo-app"]
    can_t = json.loads(env["PREDICTIVEOPS_CANARY_TARGETS"])["demo-app"]
    assert rem["container"] == SVC["demo-app"]["container_name"] and rem["action"] == "restart_container"
    assert can_t["container"] == SVC["demo-app-canary"]["container_name"]
    assert can_t["action"] == "rollback_canary"


def test_production_api_does_not_auto_train_or_promote_synthetic_models():
    trainer = SVC["trainer"]
    assert "training" in trainer["profiles"]
    command = " ".join(trainer["command"])
    assert "/srv/artifacts/data/production.csv.gz" in command
    assert "generate_data.py" not in command
    assert "--promote" in command and "--enforce-gates" in command
    assert "depends_on" not in SVC["api"]
    assert SVC["api"]["environment"]["PREDICTIVEOPS_ENV"] == "production"

def test_prometheus_jobs_match_service_names_used_by_default_queries():
    prom = load("prometheus/prometheus.yml")
    jobs = {j["job_name"]: j for j in prom["scrape_configs"]}
    assert jobs["demo-app"]["static_configs"][0]["labels"]["service"] == "demo-app"
    assert SVC["api"]["environment"]["PREDICTIVEOPS_PROMETHEUS_SERVICES"] == "demo-app"
    assert "demo-app:8001" in jobs["demo-app"]["static_configs"][0]["targets"]
    assert "api:8000" in jobs["predictiveops-api"]["static_configs"][0]["targets"]
    assert jobs["applications"]["file_sd_configs"][0]["files"] == ["/etc/prometheus/targets/*.yml"]
    assert "./prometheus/targets:/etc/prometheus/targets:ro" in SVC["prometheus"]["volumes"]
    rules = load("monitoring/alert_rules.yml")["groups"][0]["rules"]
    assert {r["alert"] for r in rules} >= {"PredictedFailure", "PolicyAlertLevel"}


def test_grafana_provisioning_consistent():
    ds = load("grafana/provisioning/datasources/prometheus.yml")["datasources"][0]
    dash = json.loads((ROOT / "grafana/dashboards/predictiveops.json").read_text())
    assert ds["uid"] == "prometheus" and ds["url"] == "http://prometheus:9090"
    for p in dash["panels"]:
        assert p["datasource"]["uid"] == ds["uid"]
        assert all(t["expr"] for t in p["targets"])
    assert len({p["id"] for p in dash["panels"]}) == len(dash["panels"])
    assert "SYNTHETIC" in dash["description"] and "not production-validated" in dash["description"]
    assert all("Demo application" not in p["title"] for p in dash["panels"])


def test_workflows_least_privilege_and_no_inline_secrets():
    for wf in (ROOT / ".github/workflows").glob("*.yml"):
        doc = load(f".github/workflows/{wf.name}")
        assert doc["permissions"] == {"contents": "read"}, wf.name
        raw = wf.read_text()
        assert "secrets." in raw or wf.name == "ci.yml"
        assert not re.search(r"uses: [^@\s]+@(main|master|latest)\b", raw), wf.name
    gates = (ROOT / ".github/workflows/ml-gates.yml").read_text()
    assert "--enforce-gates" in gates and "canary_smoke.py" in gates


def test_dockerfile_uses_non_root_user_and_pinned_base():
    d = (ROOT / "docker/Dockerfile").read_text()
    assert re.search(r"FROM python:3\.\d+-slim", d) and "USER appuser" in d and "HEALTHCHECK" in d


def test_gate_report_script(tmp_path):
    ver = tmp_path / "v1"
    ver.mkdir()
    (tmp_path / "champion.json").write_text(json.dumps({"version": "v1"}))
    t = dict(f1=0.8, precision=0.7, recall=0.9, fpr=0.01, pr_auc=0.9, brier=0.01, ece=0.01)
    meta = {"model_type": "hgb", "data_source": "synthetic", "disclaimer": "synthetic only",
            "evaluation": {"test": t,
                           "event": {"event_recall": 1.0, "detected_events": 3, "failure_events": 3},
                           "gates": {"checks": [{"name": "f1>=min", "passed": True, "value": 0.8,
                                                 "limit": 0.7}]}}}
    (ver / "metadata.json").write_text(json.dumps(meta))
    out = subprocess.run([sys.executable, "scripts/gate_report.py", str(tmp_path)], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    assert "Model `v1`" in out and "f1>=min" in out and "synthetic only" in out


def test_static_safety_net_rules_cover_real_host_telemetry():
    groups = {g["name"]: g["rules"] for g in load("monitoring/alert_rules.yml")["groups"]}
    rules = groups["host-safety-net"]
    alerts = {r["alert"] for r in rules}
    assert alerts == {"HostCpuWarning", "HostMemoryWarning", "HostFilesystemWarning", "HostDiskIoWarning"}
    assert all(r["labels"]["source"] == "static-threshold" for r in rules)
    assert all("demo_" not in r["expr"] for r in rules)
    app_rules = groups["predictiveops"]
    app_alerts = {r["alert"] for r in app_rules}
    assert {"ApplicationExporterDown", "ApplicationErrorRateWarning", "ApplicationLatencyWarning"} <= app_alerts
