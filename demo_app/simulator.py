"""Live, fault-injectable simulator behind the demo target service.

SIMULATED resource metrics: values come from the SAME baseline and fault models used to build the
training data (``data.synthetic.generator``), so the deployed model sees in-distribution inputs.
This is a demonstration environment, not a measurement of a real host.
"""

from __future__ import annotations

import threading

import numpy as np

from app.core.domain import METRIC_COLUMNS
from app.core.exceptions import DataGenerationError
from data.synthetic.generator import FAULT_TYPES, _apply_fault, _baseline

HOLD_STATE = 1.05  # progress value once the ramp completes: service stays failing until cleared/restarted


class LiveSimulator:
    def __init__(self, seed: int = 42, start_minute_of_day: int = 9 * 60) -> None:
        self._rng = np.random.default_rng(seed)
        self._base = _baseline(np.random.default_rng(seed), 1440)
        self._start = start_minute_of_day
        self._t = 0
        self._fault: dict | None = None
        self._spike_left = 0
        self._spike = (0.0, 0.0, 0.0)
        self._lock = threading.Lock()

    # ---- control ----
    def inject(self, kind: str, ramp_minutes: int = 45) -> None:
        if kind not in FAULT_TYPES:
            raise DataGenerationError(f"unknown fault type {kind!r}")
        if not 5 <= ramp_minutes <= 180:
            raise DataGenerationError("ramp_minutes must be within 5-180")
        with self._lock:
            self._fault = {"kind": kind, "ramp": ramp_minutes, "k": 0}

    def clear(self) -> None:
        with self._lock:
            self._fault = None

    def state(self) -> dict:
        with self._lock:
            f = self._fault
            return {"sim_minute": self._t, "fault": f["kind"] if f else None,
                    "fault_progress": None if not f else min((f["k"]) / f["ramp"], HOLD_STATE)}

    # ---- one simulated minute ----
    def step(self) -> dict[str, float]:
        with self._lock:
            i = (self._start + self._t) % 1440
            m = {k: np.array([v[i]], dtype=float) for k, v in self._base.items()}
            if self._fault:
                f = self._fault
                p = (f["k"] + 1) / f["ramp"] if f["k"] < f["ramp"] else HOLD_STATE
                _apply_fault(m, f["kind"], slice(0, 1), np.array([min(p, 1.0) if f["k"] < f["ramp"] else p]))
                f["k"] += 1
            if self._spike_left == 0 and self._rng.random() < 0.004:  # rare harmless transient
                self._spike_left = int(self._rng.integers(3, 8))
                self._spike = (self._rng.uniform(15, 30), self._rng.uniform(150, 450),
                               self._rng.uniform(0.005, 0.02))
            if self._spike_left > 0:
                m["cpu_pct"] += self._spike[0]
                m["latency_p95_ms"] += self._spike[1]
                m["error_rate"] += self._spike[2]
                self._spike_left -= 1
            for c in ("cpu_pct", "memory_pct", "disk_io_util_pct"):
                m[c] = np.clip(m[c], 0, 100)
            m["error_rate"] = np.clip(m["error_rate"], 0, 1)
            for c in ("network_rx_mbps", "network_tx_mbps", "request_rate_rps", "latency_p95_ms"):
                m[c] = np.clip(m[c], 0, None)
            self._t += 1
            return {c: float(m[c][0]) for c in METRIC_COLUMNS}
