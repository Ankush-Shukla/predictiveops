import httpx
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routers import history
from app.core.config import Settings


def _client(monkeypatch, handler):
    monkeypatch.setattr(
        history, "prometheus_client",
        lambda base_url: httpx.AsyncClient(base_url="http://prom", transport=httpx.MockTransport(handler)))
    return TestClient(create_app(Settings(_env_file=None, log_level="WARNING"), None, load_model=False))


def test_host_history_returns_aligned_real_points(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"status": "success", "data": {"result": [
            {"values": [[1000, "10"], [1015, "20"]]}]}})
    with _client(monkeypatch, handler) as c:
        body = c.get("/v1/host/history?minutes=15").json()
    assert body["available"] is True
    assert len(body["points"]) == 2
    assert body["latest"]["cpu_pct"] == 20.0


def test_host_history_degrades_when_prometheus_is_down(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")
    with _client(monkeypatch, handler) as c:
        body = c.get("/v1/host/history").json()
    assert body["available"] is False and body["points"] == [] and body["error"]


def test_host_history_validates_window(monkeypatch):
    with _client(monkeypatch, lambda r: httpx.Response(200, json={})) as c:
        assert c.get("/v1/host/history?minutes=1").status_code == 422


def test_infra_alerts_and_coverage(monkeypatch):
    def handler(request):
        if request.url.path == "/api/v1/alerts":
            return httpx.Response(200, json={"status": "success", "data": {"alerts": [
                {"labels": {"alertname": "HostCpuWarning", "severity": "warning"}, "state": "firing",
                 "annotations": {"summary": "cpu"}}]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": [
            {"values": [[3600, "1"], [7200, "1"]]}]}})
    with _client(monkeypatch, handler) as c:
        alerts = c.get("/v1/infra/alerts").json()
        cov = c.get("/v1/telemetry/coverage").json()
    assert alerts["firing"] == 1 and alerts["alerts"][0]["name"] == "HostCpuWarning"
    assert cov["available"] is True and cov["covered_hours"] == 2.0
