# PROJECT_STATUS

_Last updated: 2026-11-24 (Production hardening in progress)_

## Current phase
**The original prototype phases are complete; production hardening is now in progress.** The prototype's default data source and champion model were synthetic, and the model did not consume real Node Exporter telemetry. Do not treat the existing prototype as production-ready.

Current hardening work:
- Separate host and application feature schemas and training scopes.
- Remove demo-only Grafana panels and replace simulated alerts with host/application telemetry alerts.
- Prevent synthetic-only or ungated models from serving production predictions.
- Stop Compose from automatically generating and promoting synthetic models at startup.
- Add real application exporter discovery and a documented instrumentation contract.

Production release remains blocked until both scope-specific inference pipelines are wired to real telemetry, trained on reviewed incident labels, validated on held-out real events, and exercised in the target Docker environment.

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

- **Phase 7**
  - `app/services/canary_models.py` (validated `CanaryConfig`, assessment models), `canary.py` (`assess` pure decision function, `CanaryEvaluator`, `CanaryService`).
  - `PolicyEngine.request_action`: shared gated + audited path (refactor of the remediation gates; behaviour unchanged, regression-tested). `canary_runs` table in the SQLite store.
  - API: `POST /v1/canary/evaluate` (key-protected when configured), `GET /v1/canary[/{id}]`, `POST /v1/canary/simulate`; `PREDICTIVEOPS_CANARY` and `PREDICTIVEOPS_CANARY_TARGETS` settings; Prometheus `canary_evaluations_total`.
  - Dashboard: "Canary release check" (baseline vs candidate risk, decision strip, plain-language outcome, late-regression toggle).

- **Phase 3 remainder + Phase 8**
  - `demo_app/`: `LiveSimulator` (reuses the generator's baseline and fault models, so inputs are in-distribution), FastAPI target with Prometheus gauges `demo_<metric>` (SIMULATED), `/work` whose failures follow the simulated error rate, `/state`, and fault injection protected by `DEMO_ADMIN_TOKEN` (disabled when unset).
  - `app/services/prometheus_client.py` (validated service names, one expression per model metric, rows missing any metric dropped, ambiguity/errors raise), `app/api/ingestion.py` (single shared ingestion path), `app/api/poller.py` (background poller, failure-tolerant, stable logical timestamps), `GET /v1/sources`.
  - `docker/Dockerfile` (targets runtime/trainer, non-root, healthcheck), `docker-compose.yml` (trainer -> api, demo-app, optional canary + mlflow profiles, prometheus, node-exporter, grafana), `docker-compose.remediation.yml` (opt-in socket), `prometheus/prometheus.yml`, `monitoring/alert_rules.yml`, Grafana datasource/provider/dashboard (10 panels), `.github/workflows/{ci,ml-gates}.yml`, `scripts/canary_smoke.py`, `scripts/gate_report.py`, `requirements-{runtime,train}.txt`.
  - Settings: `PREDICTIVEOPS_PROMETHEUS_*`; empty environment variables are now ignored (so an empty `PREDICTIVEOPS_API_KEY` in compose means "no key", not "empty key").

### Verification matrix for Phase 8
| Item | Result |
|---|---|
| `promtool check rules` / `check config` (Prometheus 3.5.0) | passed |
| `hadolint` on the Dockerfile | clean |
| GitHub workflow schema + Compose spec schema (`check-jsonschema`) | passed |
| Static invariants (pinned images, no hardcoded secrets, no docker.sock by default, labels/targets/service names/clocks consistent, least-privilege workflows) | 10 tests pass |
| **Live end-to-end on native processes**: real Prometheus + node_exporter + demo target + API poller; fault injected; policy alert and dry-run remediation audit recorded; Prometheus scrapes the API's metrics; alert rules loaded | passed (3 min 6 s) |
| Every PromQL expression in the Grafana dashboard evaluated against that Prometheus (parse + execute) | passed |
| `scripts/canary_smoke.py` (20 seeds/type, real champion): 0 % false rollbacks, 100 % detection; exits 3 when a limit is missed | passed |
| `scripts/gate_report.py` | passed |
| `docker build`, `docker compose up`, image pulls of the pinned tags | **NOT executed** |
| Grafana actually loading the provisioning and rendering the dashboard | **NOT executed** (queries verified, UI not) |
| GitHub Actions actually running | **NOT executed** |
| Real container restart / canary stop through Docker | **NOT executed** |
| node_exporter inside a container with the `/host` mount | **NOT executed** (native binary only) |

- **Phase 9**
  - Evaluation: per-onset event records with breakdown by fault mechanism and sudden/gradual episodes (now in every training report and model metadata); `training/robustness.py` + `scripts/evaluate_robustness.py` (seed variability, held-out fault mechanism with static-threshold comparison, canary study over observation windows and fault start times); canary simulation extracted to `app/services/canary_sim.py`; full write-up in `docs/evaluation.md`.
  - Static-threshold safety-net alert rules (`monitoring/alert_rules.yml`, `promtool` OK, 9 rules) kept in sync with the baseline by a test.
  - Tests: coverage-gap tests (champion loading at startup, lazy Docker executor, 500 handler, demo clock thread, registered MLflow model loads and agrees with the predictor), robustness/event tests, automated browser test (Playwright; `tests/e2e/test_dashboard_browser.py`).

- **Phase 10**
  - README rewritten as a single coherent document (overview, architecture diagram, quick start, Compose, API, policy, canary, configuration, testing, layout, verification status, limitations).
  - `docs/requirements_traceability.md` (every synopsis objective, scope item and outcome -> modules, tests, honest status), `docs/DEMO.md` (presenter's guide), `docs/architecture.md` finalised (all components built; consolidated API table).
  - `scripts/demo_walkthrough.py`: narrated, executable tour (healthy run, memory-leak forecast, live ingestion with alerts/audit, policy preview, canary rollback/promote, canary blind spot) with in-process tests.
  - `tests/unit/test_docs.py`: links resolve, referenced files exist, honesty statements stay in the README.

## Verified (executed in the sandbox)
- Phase 9: clean virtualenv from `requirements-dev.txt`: 159 passed, 3 skipped (e2e needing external binaries/Playwright), coverage 99 %, ruff clean. Runtime-only virtualenv (`requirements-runtime.txt`, no MLflow): API loads the champion, simulate + canary + canary smoke + demo app all work. Browser e2e 4 passed. Robustness studies reproduced exactly on a second run. Final run after all changes (Phase 10, working environment): 169 passed, 2 skipped (live-stack e2e), 99 % coverage, ruff clean; the 6 e2e tests (real Prometheus + node_exporter live stack, and 4 browser tests) rerun afterwards: 6 passed in 3 min 54 s.
- Phase 7: `assess` unit tests (hold before minimum even when terrible, promote, rollback by fraction+margin, hard-fail run, not-attributable when baseline equally bad, inconclusive hold, observation limit, timestamp overlap, missing baseline); service tests (promote/hold never act; rollback dry-run, live execute once then cooldown; blocked: no target / disabled / wrong action; unconfigured service cannot reach other targets); API tests (request cannot name a container, key protection, persistence, simulate has no side effects). Dashboard canary flow driven with headless Chromium, no console errors.
- Canary simulations with the real trained model over 60 seeds per candidate type: see the measured results under Known issues. Rollbacks were recorded as dry-run only.
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

## Not verified (see matrix above)
- `DockerExecutor` has only been exercised against a fake client, never a real Docker daemon: **real restarts are unverified**.
- Frontend has no automated browser test in the repo (verified manually with Playwright only); no auth on `/v1/simulate` or `/v1/predict`.

## Known issues / caveats
- **The model does not generalise to unseen fault mechanisms** (held-out: memory leak 2/97, error ramp 2/103, CPU contention 24/108 events detected; thresholds detect all). For mechanisms it was trained on it has fewer false alerts than thresholds (0.85 % vs 1.13 % of healthy minutes on the reference split; 0.22 % vs 1.02 % on fresh data) and better minute-level recall, but event-level detection and lead are similar. Mitigation shipped: static safety-net alert rules. See `docs/evaluation.md`.
- **The canary only catches faults that start before its decision point.** Default minimum observation is 20 min (fast demo); fault starting 30 min after deploy is mostly promoted. 60+ min recommended for real use (0/150 false rollbacks at 20, 60 and 120 min).
- Event recall on the reference test split is 27/27 but from only 27 events (2 sudden); wide uncertainty.
- Deprecation warning from Starlette about `httpx` in `TestClient`; harmless for now, may need `httpx2` later.
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

- **Canary limitation:** a regression that begins after the decision is promoted (documented in a test and shown on the dashboard). The default 20-minute minimum is a trade-off; real use needs a longer period or continued monitoring (the alert policy still applies after promotion).
- Measured with the real model over 60 seeds per candidate type (synthetic, in-distribution; faults begin at minute 10): healthy candidates 60/60 promoted (0 false rollbacks); memory-leak, latency, error, CPU and disk candidates 300/300 rolled back at the first checkpoint (minute 48). This is the simulator agreeing with itself (same fault generator that trained the model, early and strong faults), so it says little about subtle or real-world regressions.
- Rollback path against a real Docker daemon is unverified (fake client only), as with remediation.

- Image tags (`prom/prometheus:v3.5.0`, `prom/node-exporter:v1.9.1`, `grafana/grafana:12.1.0`) were chosen from memory/known releases; Prometheus 3.5.0 and node_exporter 1.9.1 binaries were downloaded and run natively, Grafana was not available to run.
- The demo is **accelerated**: Prometheus scrapes every 2 s, the target ticks every 5 s, one 5 s step = one model "minute". Occasional duplicated/skipped ticks from clock drift are possible. Real-time use sets step and tick to 60 s.
- The model does **not** consume Node Exporter metrics (different scales from the synthetic training data); using real metrics requires new PromQL **and retraining on real data**. This is the largest gap between the demo and a real deployment.
- Poller runs in the API process (single instance); it re-queries a lookback window each poll rather than keeping a cursor. No Alertmanager (rules are visible in Prometheus only).
- The e2e test takes about 3 minutes and needs Prometheus/node_exporter binaries; it is skipped by default (`pytest -m e2e` with `PROMETHEUS_BIN`).

## Next steps (for the user / future work)
1. Run `docker compose up --build` (README) and report anything that fails; this is the main unexecuted item.
2. Decide whether to raise the canary default minimum observation from 20 to 60 minutes (recommended for real use).
3. Future work, out of scope for the minor project: retrain on real metrics, add data for more fault mechanisms, keep monitoring the canary after promotion, add Alertmanager.
