"""Canary simulation on SYNTHETIC scenarios (healthy baseline vs. a candidate with a chosen fault)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.models.predictor import Predictor
from app.services.canary import CanaryEvaluator, assess
from app.services.canary_models import CanaryAssessment, CanaryConfig
from data.synthetic.scenarios import generate_scenario

CHECK_EVERY = 5  # minutes between checkpoints


@dataclass
class CanarySimRun:
    baseline_scores: pd.Series
    candidate_scores: pd.Series
    candidate: pd.DataFrame
    first_scored: int  # warm-up rows without scores
    checkpoints: list[tuple[int, CanaryAssessment]]  # (minute, assessment) up to the first decision
    final: CanaryAssessment | None
    final_minute: int | None


def simulate_canary(predictor: Predictor, cfg: CanaryConfig, candidate_fault: str, *, seed: int = 42,
                    minutes: int = 180, fault_start: int = 10, ramp_minutes: int = 45) -> CanarySimRun:
    base = generate_scenario("none", minutes=minutes, seed=seed)
    cand = generate_scenario(candidate_fault, minutes=minutes, fault_start=fault_start,
                             ramp_minutes=ramp_minutes, seed=seed + 1000, min_fault_start=5)
    ev = CanaryEvaluator(predictor, cfg)
    bs, cs = ev.score(base), ev.score(cand)
    first_scored = len(base) - len(cs)
    thr = predictor.bundle.threshold
    checkpoints: list[tuple[int, CanaryAssessment]] = []
    final, final_minute = None, None
    for t in range(cfg.min_observation_minutes - 1, len(cs), CHECK_EVERY):
        a = assess(bs.iloc[: t + 1], cs.iloc[: t + 1], thr, cfg)
        checkpoints.append((first_scored + t, a))
        final, final_minute = a, first_scored + t
        if a.decision != "hold":
            break
    return CanarySimRun(bs, cs, cand, first_scored, checkpoints, final, final_minute)
