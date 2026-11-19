"""Synthetic multivariate time-series generator for PredictiveOps.

!! SYNTHETIC DATA !!  Every row carries ``data_source == "synthetic"``. Models trained on it
demonstrate the pipeline; they say nothing about real production failures.

Simulated behaviour (1-minute resolution, per independent ``series_id``):
  * healthy operation: diurnal load, AR(1) memory drift, correlated latency/CPU, noise
  * fault episodes: a gradual (or occasionally sudden) degradation ramp of one of five fault
    types, held briefly past its peak, then an instantaneous "restart" back to baseline
  * distractor spikes: short transients that must NOT lead to failure (guards against a
    model that simply learns "any spike = failure")
Ground truth ("unhealthy") is computed from the metrics via ``app.core.domain`` so labels and
metrics are consistent. Label: ``label_failure_within_h`` = 1 if a *new* unhealthy period begins
within the next ``horizon_minutes`` while the service is currently healthy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.domain import (
    METRIC_COLUMNS,
    HealthThresholds,
    compute_unhealthy,
)
from app.core.exceptions import DataGenerationError

FAULT_TYPES: tuple[str, ...] = (
    "memory_leak",
    "latency_creep",
    "error_ramp",
    "cpu_contention",
    "disk_saturation",
)
DATA_SOURCE = "synthetic"
STEP_SECONDS = 60


@dataclass(frozen=True)
class GeneratorConfig:
    days: int = 14
    n_series: int = 4
    seed: int = 42
    horizon_minutes: int = 20
    start: str = "2025-01-06T00:00:00+00:00"  # fixed => reproducible
    fault_rate_per_day: float = 3.0
    spike_rate_per_day: float = 8.0
    ramp_minutes: tuple[int, int] = (30, 90)
    sudden_fraction: float = 0.1  # episodes with a very short ramp (hard to predict)
    sudden_ramp_minutes: tuple[int, int] = (5, 10)
    hold_minutes: tuple[int, int] = (6, 15)
    min_gap_minutes: int = 90
    fault_types: tuple[str, ...] = FAULT_TYPES
    thresholds: HealthThresholds = field(default_factory=HealthThresholds)

    def validate(self) -> None:
        if self.days < 1 or self.n_series < 1:
            raise DataGenerationError("days and n_series must be >= 1")
        if not 15 <= self.horizon_minutes <= 30:
            raise DataGenerationError("horizon_minutes must be within 15-30 (synopsis)")
        if self.fault_rate_per_day < 0 or self.spike_rate_per_day < 0:
            raise DataGenerationError("rates must be >= 0")
        bad = set(self.fault_types) - set(FAULT_TYPES)
        if bad:
            raise DataGenerationError(f"unknown fault types: {sorted(bad)}")
        if not 0 <= self.sudden_fraction <= 1:
            raise DataGenerationError("sudden_fraction must be in [0, 1]")


def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float) -> np.ndarray:
    eps = rng.normal(0.0, sigma, n)
    out = np.empty(n)
    out[0] = eps[0]
    for i in range(1, n):
        out[i] = phi * out[i - 1] + eps[i]
    return out


def _baseline(rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
    minutes = np.arange(n)
    tod = (minutes % 1440) / 1440.0
    diurnal = 0.5 + 0.5 * np.sin(2 * np.pi * (tod - 0.3))
    base_rps = rng.uniform(80, 150)
    rps = base_rps * (0.6 + 0.8 * diurnal) * (1 + 0.05 * _ar1(rng, n, 0.8, 1.0))
    cpu = 12 + 25 * diurnal + rng.normal(0, 2.5, n)
    mem = 50 + np.clip(_ar1(rng, n, 0.995, 0.4), -9, 9) + 0.05 * cpu
    disk_mbps = 5 + 10 * diurnal + np.abs(rng.normal(0, 1.5, n))
    disk_util = 8 + 1.2 * disk_mbps + rng.normal(0, 1.5, n)
    rx = rps * 0.05 * (1 + rng.normal(0, 0.05, n))
    tx = rx * 1.6 * (1 + rng.normal(0, 0.05, n))
    lat = 80 * (1 + 0.5 * cpu / 100) * np.exp(rng.normal(0, 0.08, n))
    err = np.abs(0.004 + rng.normal(0, 0.0015, n))
    return {
        "cpu_pct": cpu, "memory_pct": mem, "disk_io_util_pct": disk_util,
        "network_rx_mbps": rx, "network_tx_mbps": tx, "request_rate_rps": rps,
        "latency_p95_ms": lat, "error_rate": err,
    }


def _apply_fault(m: dict[str, np.ndarray], kind: str, sl: slice, p: np.ndarray) -> None:
    """Add degradation (progress p in (0, ~1.05]) to metric arrays in-place."""
    if kind == "memory_leak":
        m["memory_pct"][sl] += 50 * p
        m["latency_p95_ms"][sl] += 300 * p**2
    elif kind == "latency_creep":
        m["latency_p95_ms"][sl] += 1500 * p**2
        m["error_rate"][sl] += 0.02 * p**3
    elif kind == "error_ramp":
        m["error_rate"][sl] += 0.09 * p**2
        m["latency_p95_ms"][sl] += 200 * p
    elif kind == "cpu_contention":
        m["cpu_pct"][sl] += 95 * p
        m["latency_p95_ms"][sl] += 1400 * p**3
    elif kind == "disk_saturation":
        m["disk_io_util_pct"][sl] += 95 * p
        m["latency_p95_ms"][sl] += 1200 * p**2
        m["error_rate"][sl] += 0.03 * p**3
    else:  # pragma: no cover - guarded by config validation
        raise DataGenerationError(f"unknown fault type {kind}")


def _generate_series(
    cfg: GeneratorConfig, rng: np.random.Generator, series_idx: int
) -> pd.DataFrame:
    n = cfg.days * 1440
    m = _baseline(rng, n)
    fault_type = np.full(n, "none", dtype=object)
    episode_id = np.full(n, -1, dtype=int)

    # --- fault episodes (non-overlapping) ---
    if cfg.fault_rate_per_day > 0:
        mean_gap = max(1440 / cfg.fault_rate_per_day - 60, 30)
        t = int(rng.integers(120, 360))
        ep = 0
        while True:
            kind = str(rng.choice(cfg.fault_types))
            lo, hi = cfg.sudden_ramp_minutes if rng.random() < cfg.sudden_fraction else cfg.ramp_minutes
            ramp = int(rng.integers(lo, hi + 1))
            hold = int(rng.integers(cfg.hold_minutes[0], cfg.hold_minutes[1] + 1))
            end = t + ramp + hold
            if end >= n - 1:
                break
            p = np.concatenate([np.linspace(1 / ramp, 1.0, ramp), np.full(hold, 1.05)])
            sl = slice(t, end)
            _apply_fault(m, kind, sl, p)
            fault_type[sl] = kind
            episode_id[sl] = series_idx * 10_000 + ep
            ep += 1
            t = end + cfg.min_gap_minutes + int(rng.exponential(mean_gap))

    # --- distractor spikes: magnitudes stay below unhealthy thresholds on their own ---
    n_spikes = int(rng.poisson(cfg.spike_rate_per_day * cfg.days))
    for st in rng.integers(0, n - 10, n_spikes):
        d = int(rng.integers(3, 8))
        sl = slice(int(st), int(st) + d)
        m["cpu_pct"][sl] += rng.uniform(15, 30)
        m["latency_p95_ms"][sl] += rng.uniform(150, 450)
        m["error_rate"][sl] += rng.uniform(0.005, 0.02)

    # --- clip to physical ranges ---
    for col in ("cpu_pct", "memory_pct", "disk_io_util_pct"):
        m[col] = np.clip(m[col], 0, 100)
    m["error_rate"] = np.clip(m["error_rate"], 0, 1)
    for col in ("network_rx_mbps", "network_tx_mbps", "request_rate_rps", "latency_p95_ms"):
        m[col] = np.clip(m[col], 0, None)

    ts = pd.date_range(cfg.start, periods=n, freq=f"{STEP_SECONDS}s")
    df = pd.DataFrame({"timestamp": ts, "series_id": f"svc-{series_idx:02d}"})
    for col in METRIC_COLUMNS:
        df[col] = m[col]
    df["is_unhealthy"] = compute_unhealthy(df, cfg.thresholds)
    df["fault_type"] = fault_type
    df["episode_id"] = episode_id
    return add_labels(df, cfg.horizon_minutes)


def add_labels(df: pd.DataFrame, horizon_minutes: int) -> pd.DataFrame:
    """Add ``label_failure_within_h`` and ``label_valid`` to ONE time-ordered series.

    label = 1  iff the service is healthy now and becomes unhealthy within (t, t+H].
    Rows already unhealthy keep label 0 (they must be excluded from training via
    ``is_unhealthy``). The last H rows have an incomplete horizon => ``label_valid`` False.
    """
    u = df["is_unhealthy"].to_numpy().astype(int)
    n = len(u)
    cs = np.concatenate([[0], np.cumsum(u)])  # cs[i] = sum(u[:i])
    idx = np.arange(n)
    hi = np.minimum(idx + horizon_minutes + 1, n)
    future = cs[hi] - cs[idx + 1]  # unhealthy count in (t, t+H]
    out = df.copy()
    out["label_failure_within_h"] = ((future > 0) & (u == 0)).astype(int)
    out["label_valid"] = idx <= n - 1 - horizon_minutes
    return out


def generate_dataset(cfg: GeneratorConfig | None = None) -> pd.DataFrame:
    """Generate the full synthetic dataset (all series concatenated, time-ordered per series)."""
    cfg = cfg or GeneratorConfig()
    cfg.validate()
    children = np.random.SeedSequence(cfg.seed).spawn(cfg.n_series)
    frames = [
        _generate_series(cfg, np.random.default_rng(children[i]), i) for i in range(cfg.n_series)
    ]
    df = pd.concat(frames, ignore_index=True)
    df["data_source"] = DATA_SOURCE
    return df


def dataset_summary(df: pd.DataFrame) -> dict:
    valid = df[df["label_valid"] & ~df["is_unhealthy"]]
    episodes = df.loc[df["episode_id"] >= 0, "episode_id"].nunique()
    return {
        "rows": int(len(df)),
        "series": int(df["series_id"].nunique()),
        "fault_episodes": int(episodes),
        "unhealthy_rate": float(df["is_unhealthy"].mean()),
        "trainable_rows": int(len(valid)),
        "positive_rate_trainable": float(valid["label_failure_within_h"].mean()) if len(valid) else 0.0,
        "data_source": DATA_SOURCE,
    }


def write_dataset(df: pd.DataFrame, path: str | Path, cfg: GeneratorConfig) -> Path:
    """Write gzip CSV plus a ``.meta.json`` sidecar (config, summary, sha256) for reproducibility."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, compression="gzip")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "data_source": DATA_SOURCE,
        "warning": "SYNTHETIC data - not real production measurements.",
        "config": json.loads(json.dumps(asdict(cfg), default=str)),
        "summary": dataset_summary(df),
        "sha256": digest,
    }
    meta_path = path.with_name(path.name.split(".")[0] + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2))
    return path


def load_dataset(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df
