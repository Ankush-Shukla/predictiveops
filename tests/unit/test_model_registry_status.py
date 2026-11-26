import json

from app.api.routers.system import _registry_status


def test_registry_status_reports_missing_champion(tmp_path):
    result = _registry_status(tmp_path, "host")

    assert result["status"] == "not_installed"
    assert result["scope"] == "host"
    assert result["version"] is None
    assert result["reason"]


def test_registry_status_reports_missing_artifact(tmp_path):
    version_dir = tmp_path / "host-v1"
    version_dir.mkdir()
    (tmp_path / "champion.json").write_text(json.dumps({"version": "host-v1"}))

    result = _registry_status(tmp_path, "host")

    assert result["status"] == "artifact_missing"
    assert result["version"] == "host-v1"


def test_registry_status_reads_metadata_without_loading_model(tmp_path):
    version_dir = tmp_path / "host-v2"
    version_dir.mkdir()
    (version_dir / "model.joblib").write_bytes(b"not deserialized")
    (version_dir / "metadata.json").write_text(json.dumps({
        "scope": "host",
        "data_source": "real",
        "trained_at": "2026-11-01T00:00:00Z",
        "threshold": 0.42,
        "feature_names": ["cpu_mean", "memory_slope"],
        "evaluation": {"pr_auc": 0.81},
    }))
    (tmp_path / "champion.json").write_text(json.dumps({"version": "host-v2"}))

    result = _registry_status(tmp_path, "host")

    assert result["status"] == "installed_unloaded"
    assert result["data_source"] == "real"
    assert result["threshold"] == 0.42
    assert result["n_features"] == 2
    assert result["evaluation"]["pr_auc"] == 0.81


def test_registry_status_rejects_scope_mismatch(tmp_path):
    version_dir = tmp_path / "application-v1"
    version_dir.mkdir()
    (version_dir / "model.joblib").write_bytes(b"not deserialized")
    (version_dir / "metadata.json").write_text(json.dumps({"scope": "application"}))
    (tmp_path / "champion.json").write_text(json.dumps({"version": "application-v1"}))

    result = _registry_status(tmp_path, "host")

    assert result["status"] == "rejected"


def test_registry_status_handles_malformed_champion_pointer(tmp_path):
    (tmp_path / "champion.json").write_text("{broken")

    result = _registry_status(tmp_path, "host")

    assert result["status"] == "invalid_registry"
