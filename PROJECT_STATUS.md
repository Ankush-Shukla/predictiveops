# PROJECT_STATUS

_Last updated: 2026-11-21 (Phase 6)_

## Current phase
Phases 1, 2, 4, 5 and 6 **complete** (the `demo_app` exporter from Phase 3 is still open). Next: Phase 7 (canary validation).

## Completed
- **Phase 1** scaffold, settings, logging, domain definitions, schemas, synthetic generator + CLI.
- **Phase 2** `docs/architecture.md` (data flow, storage, parity guarantees, evaluation protocol, API contracts).
- **Phase 4**
  - `app/features/builder.py`: 152 backward-looking features (raw, lags, rates of change, rolling mean/std/min/max, slope); versioned schema + SHA-256 hash.
  - `training/split.py` chronological split with H-minute purge gap; `baseline.py` static-threshold rule;
    `calibration.py` (sigmoid/isotonic fitted on validation only); `evaluate.py` (threshold selection under an FPR cap, calibration curve/ECE, event-level recall + lead time); `gates.py`; `tracking.py` + `pyfunc.py` (MLflow run, registered pyfunc model, `champion` alias only if gates pass); `train.py` pipeline (hgb / logreg / xgb).
  - `app/models`: `ModelBundle` (schema-hash check on load), file registry with atomic `champion.json`, `Predictor`.
  - `scripts/train.py` CLI (`--promote`, `--enforce-gates`, `--no-mlflow`).

- **Phase 5 + frontend**
  - `app/api`: app factory, routers (`/health`, `/ready`, `/v1/model`, `/v1/predict`, `/v1/metrics/ingest`, `/v1/series[/id]`, `/v1/simulate`, `/metrics`), error mapping, request-id + structured access log, optional `X-API-Key` on ingest, Prometheus collectors (per-series failure probability, predictions, ingest, HTTP counters, model info).
  - `app/services/series_buffer.py`: bounded contiguous buffer (rejects duplicates/out-of-order, clears on gaps).
  - `data/synthetic/scenarios.py`: scripted single-fault scenarios for the demo.
  - `app/static/`: dashboard (plain HTML/CSS/JS, ~13 KB, no external requests): scenario controls, probability trace vs threshold, failure/warning bands, replay scrubber, 8 metric small-multiples, synthetic-data notice. Forecast is hidden once the service is already failing (the model is not trained for that state).

- **Phase 6**
  - `app/policies`: `PolicyConfig`/`PolicyDecision`, `PolicyEngine` (levels none/warn/alert/remediate from *sustained* minutes; ordered remediation gates), `AuditStore` (SQLite: `alerts`, `audit_log`; cooldown + hourly cap read from the DB so they survive restarts), `DockerExecutor` (fixed `restart`/`stop` calls on label-verified containers only), `NoopExecutor`, lazy Docker connection.
  - Settings/env: policy thresholds, `REMEDIATION_ENABLED` (default false), `REMEDIATION_TARGETS` (JSON), existing `DRY_RUN` (default true), cooldown, hourly cap.
  - API: ingest scores every new minute (`Predictor.predict_recent`) and runs the policy in order; `/v1/policy`, `/v1/policy/evaluate`, `/v1/alerts`, `/v1/audit`; Prometheus `policy_level`, `alerts_total`, `remediation_decisions_total`; `/v1/simulate` returns a policy replay.
  - Dashboard: alert-level strip, policy summary sentence, "Send scenario to live ingestion" with alerts and audit tables. Also fixed a chart bug that joined the probability line across the failure period.

## Verified (executed in the sandbox)
- Phase 6: policy unit tests (levels, transient spikes, dry-run, every blocked outcome, cooldown, hourly cap, restart persistence, executor failure containment, Docker executor safety against a fake client) and API integration tests (scripted probability stream, real-model ingest, preview has no side effects, simulate replay never writes the real audit). Dashboard live-ingestion flow driven with headless Chromium: 360 samples ingested, alert and remediate rows and a `blocked_no_target` audit row appeared, no console errors.
- Phase 5: 72 tests pass in total (API tests use FastAPI TestClient against a really trained model); `ruff` clean.
- Dashboard driven with a headless Chromium (Playwright) against a live uvicorn process: loads, runs scenarios, plays/scrubs, no console errors, no horizontal overflow at 390 px width; desktop and mobile screenshots inspected.
- Full test suite (unit + integration, incl. real MLflow sqlite registry, XGBoost, CLI exit codes) passes; `ruff check .` clean; coverage ~97 %.
- Parity tests: no look-ahead; batch features == `transform_last`; `Predictor.predict` == batch scoring; MLflow pyfunc output == direct predictor output.
- Reference run (`generate_data --days 14 --series 4 --seed 42`, `train.py --model-type hgb`), held-out chronological test split, **SYNTHETIC data only**:

| | F1 | Precision | Recall | FPR | PR-AUC |
|---|---|---|---|---|---|
| HGB (calibrated, thr 0.195) | 0.821 | 0.781 | 0.865 | 0.0085 | 0.902 |
| Static thresholds (default warn levels) | 0.676 | 0.677 | 0.676 | 0.0113 | 0.758 |
| Static thresholds (scale tuned on val) | 0.680 | 0.662 | 0.698 | 0.0125 | 0.758 |

  Calibration: Brier 0.0079 vs 0.0327 for always predicting prevalence; ECE 0.0038. Event level: 27/27 failure onsets had an alert in the preceding 20 min (median lead 20 min); ~11.4 false-alert rows per series-day.
  A model trained on seed 42 scored on a freshly simulated seed-777 dataset: F1 0.816, FPR 0.011.
- Gates (F1 ≥ 0.70, FPR ≤ 0.05, PR-AUC > baseline) pass on this run.

## Not verified / not started
- Docker, Compose, Prometheus, Grafana, GitHub Actions (no Docker in sandbox; Phase 8).
- `DockerExecutor` has only been exercised against a fake client, never a real Docker daemon: **real restarts are unverified**.
- No canary logic or `demo_app` exporter yet; the API is not yet fed by Prometheus.
- Frontend has no automated browser test in the repo (verified manually with Playwright only); no auth on `/v1/simulate` or `/v1/predict`.

## Known issues / caveats
- All numbers come from a simulator whose fault mechanics are simple; they show the pipeline works, **not** that real failures are predictable. The ML advantage over thresholds is real but modest (F1 0.82 vs 0.68) on this data.
- 27 test failure events is a small sample; event recall of 100 % has wide uncertainty. Not yet broken down by fault type or sudden vs gradual faults, and no held-out fault type experiment.
- "Unhealthy" requires 3 sustained minutes, so onsets (and labels) are slightly delayed vs. first threshold breach.
- ~11 false-alert rows per series-day: policy engine (Phase 6) must require sustained probability before alerting.
- joblib bundles are pickles: load only your own artifacts.
- MLflow logs a pyfunc model with `code_paths` (copies repo code per run) — acceptable for a demo, heavy for production.
- MLflow emits non-fatal warnings (cloudpickle, missing signature).
- Phase 3 remainder (`demo_app` exporter) is still open.

- `/v1/simulate` is a demo endpoint that scores generated data; it is not evidence of real-world accuracy. Series buffers and latest predictions are in memory only (lost on restart).
- `/v1/predict` accepts up to 1440 samples; no rate limiting yet.

- Policy reduces but does not remove false alerts: on the reference scenarios alert-minutes outside the 20-minute window fell only from 6 to 4 (memory leak) and 9 to 7 (CPU). Most of these are alerts earlier than 20 minutes before a real developing fault, which the label definition counts as false.
- Policy state (counters, levels) is in memory; audit/alerts persist in SQLite but counters restart from zero after a restart. Single-process only (SQLite single connection + lock).
- `/v1/policy/evaluate` and `/v1/simulate` are unauthenticated and unthrottled.

## Next steps
1. Phase 7: `CanaryEvaluator` (baseline vs candidate scored by the same model, configurable thresholds, minimum observation period, hold on insufficient data, rollback only of labelled canary containers) + rollback simulation; `/v1/canary/*`; dashboard panel.
2. Phase 3 remainder: `demo_app` with Prometheus metrics + fault injection; Prometheus query client feeding the API.
3. Phase 8: Docker Compose, Prometheus, Grafana, GitHub Actions (written, validated statically, flagged unexecuted here).
4. Add per-fault-type and held-out-fault evaluation to the training report.
