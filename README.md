# PredictiveOps

Predictive failure detection and ML-driven deployment validation for containerized applications
(MCA Semester I minor project — see `PROJECT_STATUS.md` for current progress).

> **Data notice:** all datasets produced by this repository's generator are **synthetic**
> (`data_source = "synthetic"`). They demonstrate the pipeline and say nothing about real
> production failures. No model accuracy is claimed anywhere without evaluation evidence.

## Quick start (Phase 1)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                      # optional; all settings have safe defaults

python scripts/generate_data.py --days 14 --series 4 --seed 42 \
    --out data/generated/synthetic.csv.gz  # writes .csv.gz + .meta.json (config, summary, sha256)

pytest                                    # 27 tests
pytest --cov=app --cov=data               # coverage
ruff check .                              # lint
```

## Layout

| Path | Purpose |
|---|---|
| `app/core` | settings (env-based), structured logging, exceptions, shared domain definitions |
| `app/models` | Pydantic schemas and metric-frame validation |
| `app/{api,features,services,policies}` | reserved for later phases |
| `data/synthetic` | synthetic time-series generator and labelling |
| `training`, `demo_app`, `monitoring`, `prometheus`, `grafana`, `docker`, `.github/workflows` | later phases |
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
