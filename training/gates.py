"""CI/CD quality gates: only models that pass are promoted to champion."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class GateConfig:
    min_f1: float = 0.70
    max_fpr: float = 0.05
    min_event_recall: float = 0.0  # optional extra gate (off by default)
    must_beat_baseline_pr_auc: bool = True


class GateCheck(BaseModel):
    name: str
    passed: bool
    value: float
    limit: float


class GateResult(BaseModel):
    passed: bool
    checks: list[GateCheck]


def evaluate_gates(test_metrics: dict, event: dict, baseline_pr_auc: float, cfg: GateConfig) -> GateResult:
    """Gates are evaluated on the held-out TEST segment only."""
    checks = [
        GateCheck(name="f1>=min", passed=test_metrics["f1"] >= cfg.min_f1,
                  value=test_metrics["f1"], limit=cfg.min_f1),
        GateCheck(name="fpr<=max", passed=test_metrics["fpr"] <= cfg.max_fpr,
                  value=test_metrics["fpr"], limit=cfg.max_fpr),
    ]
    if cfg.min_event_recall > 0:
        er = event["event_recall"]
        checks.append(GateCheck(name="event_recall>=min", passed=er >= cfg.min_event_recall,
                                value=er, limit=cfg.min_event_recall))
    if cfg.must_beat_baseline_pr_auc:
        checks.append(GateCheck(name="pr_auc>baseline", passed=test_metrics["pr_auc"] > baseline_pr_auc,
                                value=test_metrics["pr_auc"], limit=baseline_pr_auc))
    return GateResult(passed=all(c.passed for c in checks), checks=checks)
