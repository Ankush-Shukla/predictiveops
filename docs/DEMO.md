# Demo guide

All data is **synthetic**. Say so while presenting. Total time: about 10 minutes.

## Setup (once)
```bash
pip install -r requirements-dev.txt
python scripts/generate_data.py --days 14 --series 4 --out data/generated/synthetic.csv.gz
python scripts/train.py --promote                  # prints metrics and passes the gates
uvicorn app.api.main:app_factory --factory --port 8000
```
Open http://localhost:8000. (With Docker: see the README; Grafana at :3000.)

## Scenario 1: forecast before failure (dashboard, 2 min)
1. Fault = *Memory leak*, **Run scenario**. Headline: *Warned N minutes before the service failed*.
2. Drag the scrubber to the rising part of the trace: risk climbs while the service is still healthy; the
   strip below turns from grey (warn) to red (alert), then dark red (remediation planned).
3. Point out: the line disappears once the service is failing (the model is only defined while healthy).
4. Switch to *None*: harmless spikes produce no policy alerts.

## Scenario 2: alerts and audit trail (1 min)
**Send scenario to live ingestion**. The tables show `alert`, `remediate`, `resolved`, and a remediation decision.
By default it is `blocked_no_target` (remediation is off). To show a dry-run, set
`PREDICTIVEOPS_REMEDIATION_ENABLED=true` and `PREDICTIVEOPS_REMEDIATION_TARGETS={"demo-live":{"container":"x","action":"restart_container"}}`
and rerun: the outcome becomes `dry_run`; a second attempt within 15 minutes is `skipped_cooldown`.

## Scenario 3: canary (2 min)
1. *A memory leak* -> **Rolled back after 20 minutes of observation** (recorded in dry-run).
2. *A healthy release* -> **Promoted**.
3. *A memory leak* + tick **Problem begins after the observation window** -> promoted, with the sentence that
   the release did fail later. This is the canary's blind spot; say so (`docs/evaluation.md`, section 4).

## Scenario 4: the live pipeline (Docker or native, 3 min)
```bash
docker compose up --build        # or run the native e2e: pytest -m e2e (needs Prometheus binaries)
curl -X POST localhost:8001/fault -H "X-Admin-Token: $DEMO_ADMIN_TOKEN" -H 'content-type: application/json' \
     -d '{"fault_type":"memory_leak","ramp_minutes":30}'
curl localhost:8000/v1/alerts ; curl localhost:8000/v1/audit
```
Watch Grafana (*PredictiveOps* dashboard): failure probability rises, policy level climbs, the injected-fault
panel lights up. In Prometheus, the `PredictedFailure` and the static `Static*Warning` rules appear under Alerts.

## One-command narrated run
```bash
python scripts/demo_walkthrough.py --base-url http://localhost:8000
```

## Questions to expect, and honest answers
* *Does it predict real failures?* Unknown. It is evaluated only on a simulator written for this project.
* *Why keep static thresholds?* The model missed mechanisms absent from training (held-out study); thresholds caught them.
* *Is auto-remediation safe?* Off by default, dry-run by default, two allowlisted actions, cooldown, hourly cap,
  audit log; the real Docker path has not been run by the author.
* *Why is the default canary window only 20 minutes?* So the demo decides quickly; use 60 minutes or more for real use.
