"""Application state: model, series buffers, Prometheus collectors."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from prometheus_client import CollectorRegistry, Counter, Gauge

from app.core.config import Settings
from app.models.predictor import Predictor
from app.policies.actions import LazyDockerExecutor, NoopExecutor
from app.policies.audit import AuditStore
from app.policies.engine import PolicyEngine
from app.services.series_buffer import SeriesBuffer


class Collectors:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.probability = Gauge("predictiveops_failure_probability",
                                 "Latest calibrated P(failure within horizon)", ["series_id"],
                                 registry=registry)
        self.alert = Gauge("predictiveops_predicted_failure",
                           "1 if latest probability >= model threshold", ["series_id"],
                           registry=registry)
        self.predictions = Counter("predictiveops_predictions_total", "Predictions served",
                                   ["source"], registry=registry)
        self.ingested = Counter("predictiveops_ingested_samples_total", "Samples accepted",
                                registry=registry)
        self.requests = Counter("predictiveops_http_requests_total", "HTTP requests",
                                ["method", "route", "status"], registry=registry)
        self.policy_level = Gauge("predictiveops_policy_level",
                                  "Alert level per series (0 none, 1 warn, 2 alert, 3 remediate)",
                                  ["series_id"], registry=registry)
        self.alerts = Counter("predictiveops_alerts_total", "Alerts raised", ["level"], registry=registry)
        self.actions = Counter("predictiveops_remediation_decisions_total",
                               "Remediation decisions by outcome", ["outcome"], registry=registry)
        self.canary = Counter("predictiveops_canary_evaluations_total", "Canary evaluations",
                              ["decision"], registry=registry)
        self.model_info = Gauge("predictiveops_model_info", "Loaded model", ["version", "data_source"],
                                registry=registry)


@dataclass
class ApiState:
    settings: Settings
    predictor: Predictor | None
    scope_predictors: dict[str, Predictor] = field(default_factory=dict)
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    buffers: dict[str, SeriesBuffer] = field(default_factory=dict)
    latest: dict[str, object] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    collectors: Collectors = field(init=False)
    store: AuditStore = field(init=False)
    policy: PolicyEngine = field(init=False)

    def __post_init__(self) -> None:
        self.collectors = Collectors(self.registry)
        cfg = self.settings.policy_config()
        self.store = AuditStore(self.settings.sqlite_path)
        live = cfg.remediation_enabled and not cfg.dry_run
        self.policy = PolicyEngine(cfg, self.store, LazyDockerExecutor() if live else NoopExecutor())
        self.set_predictor(self.predictor)

    def set_predictor(self, predictor: Predictor | None) -> None:
        self.predictor = predictor
        if predictor is not None:
            scope = str(predictor.bundle.metadata.get("scope", "combined"))
            self.scope_predictors[scope] = predictor
            self.collectors.model_info.labels(
                version=predictor.version,
                data_source=str(predictor.bundle.metadata.get("data_source", "unknown"))).set(1)

    def set_scope_predictors(self, predictors: dict[str, Predictor]) -> None:
        self.scope_predictors = dict(predictors)
        for predictor in self.scope_predictors.values():
            self.collectors.model_info.labels(
                version=predictor.version,
                data_source=str(predictor.bundle.metadata.get("data_source", "unknown"))).set(1)
