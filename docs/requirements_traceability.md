# Synopsis traceability

Maps every objective, scope item and expected outcome of the MCA synopsis ("PredictiveOps") to its
implementation, its tests and an honest status. **Done** = implemented and tested here. **Done, partly unverified** =
implemented and statically validated or tested against fakes, but not exercised against the real thing.
All ML results are on SYNTHETIC data (see `docs/evaluation.md`).

## Objectives

| # | Objective (synopsis) | Implementation | Tests / evidence | Status |
|---|---|---|---|---|
| 1 | Collect multivariate time-series metrics (CPU, memory, disk I/O, network, latency, error rate, request rate) from containerized apps | `demo_app/` (simulated gauges + real `/work`), `prometheus/prometheus.yml`, `app/services/prometheus_client.py`, `app/api/poller.py`, `app/services/series_buffer.py` | `tests/unit/test_demo_app.py`, `tests/integration/test_prometheus_ingest.py`, live-stack e2e with real Prometheus and node_exporter | Done, partly unverified (never run inside Docker; model consumes simulated metrics, not Node Exporter) |
| 2 | ML model predicting failure probability 15-30 min ahead | `app/features/`, `training/` (hgb, logreg, xgboost; calibrated), `app/models/` | `tests/unit/test_features.py`, `tests/integration/test_training_pipeline.py`; `docs/evaluation.md` | Done (synthetic data only; does not generalise to unseen fault mechanisms) |
| 3 | Model in CI/CD with quality gates so only sufficiently accurate models are promoted | `training/gates.py`, `scripts/train.py --enforce-gates`, `.github/workflows/ml-gates.yml`, `scripts/canary_smoke.py` | gate unit + pipeline tests (failed gates block promotion), CLI exit-code test | Done, partly unverified (workflows validated by schema only, never run on GitHub) |
| 4 | Constrained automated response (container restart / canary rollback) from high-confidence predictions | `app/policies/` (engine, allowlist, cooldown, hourly cap, dry-run, SQLite audit), `app/services/canary.py` | `tests/unit/test_policies.py`, `tests/unit/test_canary.py`, API policy/canary tests | Done, partly unverified (Docker executor tested against a fake client only; off + dry-run by default) |
| 5 | Demonstrate the lifecycle: collection -> prediction -> alerting/remediation -> deployment validation | `scripts/demo_walkthrough.py`, dashboard (`app/static/`), Compose stack | `tests/e2e/test_live_stack.py`, `tests/e2e/test_dashboard_browser.py`, `tests/integration/test_demo_walkthrough.py` | Done (native processes); Docker path unexecuted |

## Scope

| In scope (synopsis) | Where |
|---|---|
| Docker + Docker Compose monitoring | `docker/Dockerfile`, `docker-compose.yml` (validated statically; not executed) |
| Prometheus + Node Exporter metrics | `prometheus/`, compose services; both run natively in the e2e test |
| Feature engineering (rolling, lag, rate of change, aggregates) | `app/features/builder.py` (152 features, hashed schema) |
| scikit-learn / XGBoost model | `training/train.py` (`hgb`, `logreg`, `xgb`) |
| MLflow versioning and tracking | `training/tracking.py`, `training/pyfunc.py` (runs, registry, `champion` alias) |
| GitHub Actions quality gates (F1, FPR) | `.github/workflows/ml-gates.yml`, `training/gates.py` |
| Simple policy engine, constrained remediation | `app/policies/` |
| Canary validation with the same model | `app/services/canary*.py`, `POST /v1/canary/*` |
| Grafana visualisation | `grafana/` (queries executed against Prometheus; Grafana itself not run) |

| Out of scope (synopsis) | Respected how |
|---|---|
| Production Kubernetes orchestration | Compose only |
| Unrestricted command execution | Two allowlisted actions on label-verified containers; no shell anywhere |
| Large-scale tracing / dependency graphs | none |
| Very-high-throughput streaming inference | one-minute batch scoring |

## Expected outcomes

| # | Outcome | Status |
|---|---|---|
| 1 | Working prototype predicting failure probability from multivariate metrics | Done (`POST /v1/predict`, ingestion, dashboard) |
| 2 | Proactive alerts before the system is fully unhealthy | Done on synthetic data: median lead 16-20 min per mechanism on the reference split |
| 3 | Model quality gates in the CI pipeline | Done (workflow not yet run on GitHub) |
| 4 | Constrained remediation (restart / rollback) | Done in dry-run; real execution unverified |
| 5 | Predictive canary validation | Done; blind to faults starting after its decision point (quantified) |
| 6 | Grafana dashboards of health and failure probability | Done as provisioning + dashboard JSON; queries verified, UI render not |

## Master-prompt engineering requirements (selected)

| Requirement | Evidence |
|---|---|
| Chronological split without leakage | `training/split.py` purge gap; `tests/unit/test_training_parts.py`; no-look-ahead feature test |
| Training/inference use identical features | hashed `FeatureSchema`, parity tests, schema-mismatch refusal |
| Class imbalance and calibration | class weights; validation-only sigmoid/isotonic; ECE and Brier reported |
| Never fabricate accuracy | every number in the docs is produced by a command in `docs/evaluation.md`; limitations stated |
| Synthetic data clearly distinguished | `data_source` column, metadata sidecar, model metadata, API field, UI notice |
| Secrets via env / GitHub secrets | no defaults for required secrets; static invariant test scans for key patterns |
| Allowlist, dry-run, cooldown, audit, limits | `app/policies/`, safe-by-default compose, opt-in override file |
| Unit, integration, end-to-end tests | 160+ tests; 6 e2e (live stack, browser) |
