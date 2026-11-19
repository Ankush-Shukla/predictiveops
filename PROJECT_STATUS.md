# PROJECT_STATUS

_Last updated: 2026-11-19_

## Current phase
Phase 1 (scaffold, configuration, synthetic data foundation) — **complete**. Awaiting approval for Phase 2/4.

## Completed
- Repository skeleton per approved architecture (`app/*`, `data/`, `training/`, `demo_app/`, `tests/*`, infra dirs).
- `pyproject.toml`, `requirements*.txt`, `.env.example`, `.gitignore`, ruff and pytest config.
- `app/core`: env-based `Settings` (horizon validated 15–30, dry-run default), JSON/key-value structured logging, exception hierarchy, shared domain (`METRIC_COLUMNS`, `HealthThresholds`, `compute_unhealthy`).
- `app/models/schemas.py`: `MetricSample` and `validate_metrics_frame`.
- `data/synthetic/generator.py`: reproducible multivariate generator (8 metrics, 5 fault types, sudden faults, distractor spikes), labelling, writer with metadata sidecar (sha256), loader.
- `scripts/generate_data.py` CLI.
- Tests: 27 (unit + CLI integration); coverage of generator 100 %.

## Verified (executed in sandbox)
- `pytest` passes; `ruff check .` clean; CLI generates 14-day / 4-series dataset
  (80,640 rows, ~3.4 % positive rate on trainable rows with defaults — a property of the simulator, not a model result).

## Not verified / not started
- Docker, Compose, Prometheus, Grafana, GitHub Actions (Docker is not available in the sandbox; Phases 8+).
- No ML model, API, policy engine or canary logic exists yet.

## Known issues / notes
- Repository was empty at start (greenfield); nothing pre-existing to preserve.
- Gate thresholds (F1 ≥ 0.70, FPR ≤ 0.05) are provisional until real evaluation results exist.
- Unhealthy onset in the generator is declared after 3 sustained minutes, so labels use that delayed onset.
- Dataset is CSV (gzip); switch to parquet later only if size becomes a problem.

## Next steps
1. Phase 2: write `docs/architecture.md` (diagram, API contracts, storage strategy).
2. Phase 4: `FeatureBuilder` (rolling/lag/rate/aggregates) with versioned schema, chronological split with horizon gap,
   baseline threshold rule, logistic regression, gradient boosting, calibration, MLflow tracking.
3. Phase 3 remainder: `demo_app` exporter + Prometheus-format metrics.
