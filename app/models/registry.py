"""File-based model store with a 'champion' pointer (MLflow registry mirrors it in training)."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.bundle import ModelBundle, ModelNotFoundError

CHAMPION_FILE = "champion.json"


def save_version(bundle: ModelBundle, root: Path, version: str) -> Path:
    path = Path(root) / version
    if path.exists():
        raise FileExistsError(f"model version already exists: {version}")
    return bundle.save(path)


def set_champion(root: Path, version: str) -> None:
    root = Path(root)
    if not (root / version / "model.joblib").exists():
        raise ModelNotFoundError(f"unknown model version {version}")
    tmp = root / (CHAMPION_FILE + ".tmp")
    tmp.write_text(json.dumps({"version": version}))
    tmp.replace(root / CHAMPION_FILE)  # atomic swap


def champion_version(root: Path) -> str | None:
    f = Path(root) / CHAMPION_FILE
    return json.loads(f.read_text())["version"] if f.exists() else None


def list_versions(root: Path) -> list[str]:
    root = Path(root)
    return sorted(p.name for p in root.iterdir() if (p / "model.joblib").exists()) if root.exists() else []


def load_champion(root: Path) -> tuple[str, ModelBundle]:
    v = champion_version(root)
    if v is None:
        raise ModelNotFoundError(f"no champion model in {root}")
    return v, ModelBundle.load(Path(root) / v)
