"""Browser test of the dashboard against a real uvicorn process (Playwright + Chromium).

Skipped when Playwright/Chromium are unavailable. Run with:
    pytest -m e2e -o addopts="" tests/e2e/test_dashboard_browser.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.gates import GateConfig
from training.train import TrainConfig, run_training

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.e2e


def wait_text(page, selector: str, text: str, timeout: int = 20000) -> None:
    page.wait_for_function("([s, t]) => document.querySelector(s).textContent.includes(t)",
                           arg=[selector, text], timeout=timeout)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("browser")
    df = generate_dataset(GeneratorConfig(days=14, n_series=4, seed=42))
    res = run_training(df, TrainConfig(model_type="hgb", model_root=tmp / "models", use_mlflow=False,
                                       promote=True, gates=GateConfig(min_f1=0.5, max_fpr=0.1)))
    assert res.promoted
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = open(tmp / "api.log", "w")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app_factory", "--factory", "--port", str(port),
         "--log-level", "warning"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PREDICTIVEOPS_MODEL_DIR": str(tmp / "models"),
             "PREDICTIVEOPS_SQLITE_PATH": str(tmp / "a.sqlite"), "PREDICTIVEOPS_LOG_LEVEL": "WARNING"})
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise AssertionError("API did not become ready")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        log.close()


@pytest.fixture(scope="module")
def page(server):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium unavailable: {exc}")
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        pg.errors = []  # type: ignore[attr-defined]
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        pg.goto(server + "/")
        yield pg
        browser.close()


def test_dashboard_runs_scenarios_and_policy(page):
    page.wait_for_selector("#headline:has-text('minutes')", timeout=20000)
    assert "synthetic" in page.inner_text("#model")
    assert page.inner_text("#sub").strip() and page.inner_text("#sub2").strip()  # model + policy summaries
    page.select_option("#fault", "none")
    page.click("#run")
    page.wait_for_function("document.querySelector('#headline').textContent.includes('run with no failure')")
    page.select_option("#fault", "cpu_contention")
    page.click("#run")
    page.wait_for_function("document.querySelector('#headline').textContent.match(/Warned|not predicted/)")
    page.fill("#slider", "10")
    page.dispatch_event("#slider", "input")
    assert "failure risk" in page.inner_text("#readout") or "collecting" in page.inner_text("#readout")
    page.click("#play")
    page.wait_for_timeout(400)
    page.click("#play")


def test_live_ingestion_fills_alert_and_audit_tables(page):
    page.select_option("#fault", "memory_leak")
    page.click("#run")
    wait_text(page, "#headline", "Warned", 20000)
    page.click("#sendlive")
    page.wait_for_function("document.querySelector('#livemsg').textContent.startsWith('Sent')", timeout=60000)
    assert "Sent 360 samples" in page.inner_text("#livemsg")
    page.wait_for_selector("#alerts table")
    assert "demo-live" in page.inner_text("#alerts")
    assert "blocked_no_target" in page.inner_text("#audit")  # remediation is off/unconfigured by default


def test_canary_panel_and_limitation_toggle(page):
    page.select_option("#canfault", "memory_leak")
    page.click("#canrun")
    wait_text(page, "#canhead", "Rolled back", 20000)
    assert "dry-run" in page.inner_text("#cansub")
    page.select_option("#canfault", "none")
    page.click("#canrun")
    wait_text(page, "#canhead", "Promoted", 20000)
    page.select_option("#canfault", "memory_leak")
    page.check("#canlate")
    page.click("#canrun")
    wait_text(page, "#cansub", "too short", 20000)


def test_no_js_errors_and_responsive_layout(page):
    assert page.errors == []
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.set_viewport_size({"width": 1280, "height": 900})
