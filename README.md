# PredictiveOps

Predictive failure detection and ML-driven deployment validation for containerized applications
(MCA Semester I minor project — see `PROJECT_STATUS.md` for current progress).

> **Data notice:** all datasets produced by this repository's generator are **synthetic**
> (`data_source = "synthetic"`). They demonstrate the pipeline and say nothing about real
> production failures. No model accuracy is claimed anywhere without evaluation evidence.

## Quick start (Phases 1-4)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                      # optional; all settings have safe defaults

python scripts/generate_data.py --days 14 --series 4 --seed 42 \
    --out data/generated/synthetic.csv.gz  # writes .csv.gz + .meta.json (config, summary, sha256)

python scripts/train.py --data data/generated/synthetic.csv.gz --model-type hgb --promote
#   --model-type {hgb,logreg,xgb}  --enforce-gates (exit 2 if gates fail, for CI)  --no-mlflow
#   -> artifacts/models/<version>/ , champion.json , MLflow run + registry (artifacts/mlflow.db)
mlflow ui --backend-store-uri sqlite:///artifacts/mlflow.db   # browse experiments

pytest -o addopts=""                      # full suite (~75 s; includes MLflow, training, API)
pytest --cov=app --cov=data               # coverage
ruff check .                              # lint
```

## Run the API and dashboard (Phase 5)

```bash
python scripts/train.py --promote            # a champion model must exist (see above)
uvicorn app.api.main:app_factory --factory --port 8000
# Dashboard:  http://localhost:8000/        Swagger UI: http://localhost:8000/docs
```

The dashboard runs a **synthetic** scenario (pick a fault type), scores every minute with the champion model
through `POST /v1/simulate`, and shows the failure-probability trace against the alert threshold, the
true failure period, and the underlying metrics, with a replay scrubber.

```bash
curl localhost:8000/ready
curl localhost:8000/v1/model
curl -X POST localhost:8000/v1/simulate -H 'content-type: application/json' \
     -d '{"fault_type":"memory_leak","seed":42}'
# Score a window (>= 30 contiguous 1-minute samples of ONE service):
curl -X POST localhost:8000/v1/predict -H 'content-type: application/json' -d @window.json
# Live ingestion (sends X-API-Key only if PREDICTIVEOPS_API_KEY is set):
curl -X POST localhost:8000/v1/metrics/ingest -H 'content-type: application/json' \
     -d '{"series_id":"checkout","samples":[{"timestamp":"2025-01-01T00:00:00Z","cpu_pct":20,"memory_pct":50,"disk_io_util_pct":10,"network_rx_mbps":4,"network_tx_mbps":6,"request_rate_rps":90,"latency_p95_ms":110,"error_rate":0.004}]}'
curl localhost:8000/metrics                  # Prometheus exposition (failure probability per series)
```

Ingestion rules: samples must be 1 minute apart; duplicates/out-of-order samples are rejected; a gap clears the
series history (features need regular spacing). The model is loaded from `PREDICTIVEOPS_MODEL_DIR` at startup;
without one the API still starts, `/ready` returns 503 and prediction endpoints return `model_not_ready`.

## Alert policy and safe remediation (Phase 6)

Each scored minute passes through `app/policies/engine.py`:

| Level | Condition |
|---|---|
| none | probability below the model threshold |
| warn | at/above threshold, not yet sustained |
| alert | at/above threshold for `POLICY_SUSTAIN_ALERT` consecutive minutes (default 3) |
| remediate | at/above `max(0.80, threshold)` for `POLICY_SUSTAIN_REMEDIATE` minutes (default 5) |

Remediation is **off by default**. To try it, set in `.env`:
`PREDICTIVEOPS_REMEDIATION_ENABLED=true` and `PREDICTIVEOPS_REMEDIATION_TARGETS={"demo-live":{"container":"my-app","action":"restart_container"}}`.
It still **only records** decisions while `PREDICTIVEOPS_DRY_RUN=true` (the default). Safety layers, in order:
explicit series→container target, action allowlist (`restart_container`, `rollback_canary` only), master switch,
per-target cooldown (900 s), global actions-per-hour cap, dry-run, and (live mode) the container must carry the
label `predictiveops.managed=true` (`rollback_canary` also needs `predictiveops.role=canary`). There is no
generic command execution anywhere. Cooldowns and caps are read from the SQLite audit log, so they survive restarts.
Every distinct outcome (`executed`, `dry_run`, `blocked_*`, `skipped_cooldown`, `skipped_rate_limit`, `failed`) is audited.

```bash
curl localhost:8000/v1/policy                      # active policy + live per-series state
curl localhost:8000/v1/alerts ; curl localhost:8000/v1/audit
curl -X POST localhost:8000/v1/policy/evaluate -H 'content-type: application/json' \
     -d '{"probabilities":[0.9,0.9,0.9,0.9,0.9,0.9],"threshold":0.2}'   # side-effect-free preview
```

## Layout

| Path | Purpose |
|---|---|
| `app/core` | settings (env-based), structured logging, exceptions, shared domain definitions |
| `app/models` | Pydantic schemas and metric-frame validation |
| `app/features` | `FeatureBuilder` (152 backward-looking features, hashed schema) |
| `app/models` | `ModelBundle`, file registry (champion pointer), `Predictor` |
| `app/api` | FastAPI app factory, routers (system, predict/ingest, simulate), errors, state |
| `app/static` | dashboard frontend (plain HTML/CSS/JS, no build step, no external requests) |
| `app/services` | `SeriesBuffer` (live ingestion buffer); Prometheus client, canary, remediation planned |
| `app/policies` | policy engine, allowlisted actions, SQLite audit store |
| `data/synthetic` | synthetic time-series generator and labelling |
| `training` | split, baseline, calibration, evaluation, gates, MLflow, pipeline |
| `docs/architecture.md` | architecture, data flow, evaluation protocol |
| `demo_app`, `monitoring`, `prometheus`, `grafana`, `docker`, `.github/workflows` | later phases |
| `scripts` | command-line entry points |
| `tests/{unit,integration,e2e}` | test suites |

## Design notes (Phase 1)

* **One definition of "unhealthy"** (`app/core/domain.py`): error rate ≥ 5 %, p95 latency ≥ 1000 ms,
  memory ≥ 95 %, CPU ≥ 98 % or disk-IO util ≥ 98 %, sustained for 3 consecutive minutes.
* **Label** `label_failure_within_h`: service is healthy now **and** becomes unhealthy within the next
  H minutes (H = 15–30, default 20). Rows already unhealthy are excluded from training via
  `is_unhealthy`; the last H rows of a series have `label_valid = False`.
* **Fault types:** memory leak, latency creep, error ramp, CPU contention, disk saturation, plus a
  fraction of *sudden* faults and *distractor spikes* that never lead to failure.
* **Reproducible:** fixed seed and start time; per-series RNG streams via `SeedSequence`.
* Settings come from `PREDICTIVEOPS_*` environment variables; remediation defaults to **dry-run**.
