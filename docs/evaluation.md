# Evaluation and limitations

**Everything here is measured on SYNTHETIC data from this project's own simulator.** It shows how the pipeline
and model behave under controlled conditions. It is not evidence that the model predicts failures of real systems.
All numbers below were produced by the commands at the end and reproduce exactly (fixed seeds).

## 1. Reference model (hgb, seed 42, chronological split with purge gap)

Held-out test segment: F1 0.821, precision 0.781, recall 0.865,
false-positive rate 0.0085, PR-AUC 0.902, Brier 0.0079, ECE 0.0038.
Static warning thresholds on the same segment: F1 0.676, false-positive rate 0.0113.

Event level (a failure counts as detected if an alert fired in the 20 minutes before it):

| fault mechanism | detected | median lead |
|---|---|---|
| cpu_contention | 6/6 | 19 min |
| disk_saturation | 4/4 | 18 min |
| error_ramp | 6/6 | 16 min |
| latency_creep | 6/6 | 20 min |
| memory_leak | 5/5 | 20 min |

| episode speed | detected | median lead |
|---|---|---|
| gradual | 25/25 | 20 min |
| sudden | 2/2 | 6 min |

Caveat: 27 test events in total; only 2 were sudden faults. These counts are small and the intervals are wide.
The perfect event recall on this test split should not be read as "never misses".

## 2. Seed variability (5 independently simulated datasets)

| metric | mean | std | min | max |
|---|---|---|---|---|
| f1 | 0.849 | 0.034 | 0.783 | 0.875 |
| fpr | 0.003 | 0.001 | 0.002 | 0.004 |
| pr_auc | 0.885 | 0.024 | 0.850 | 0.916 |
| event_recall | 1.000 | 0.000 | 1.000 | 1.000 |
| baseline_f1 | 0.694 | 0.008 | 0.682 | 0.707 |
| baseline_pr_auc | 0.756 | 0.018 | 0.727 | 0.782 |

The F1 advantage over static thresholds is stable across data draws (about 0.15). It comes from both higher
precision and higher minute-level recall. At the event level, thresholds detect as many failures with similar lead
times (see section 3), so the practical gain is fewer, better-timed alert minutes, not catching failures that
thresholds would miss. The size of the false-alert reduction depends on the evaluation: on the reference split
0.85 % vs 1.13 % of healthy minutes (about 25 % fewer); on fresh data of trained mechanisms (section 3) 0.22 %
vs 1.02 % (about 4x fewer).

## 3. Held-out fault mechanism

Train without one mechanism, test on data containing only it (unseen) vs. fresh data of the mechanisms it was trained on (seen).

| held out | model detected (unseen) | model lead | model FPR | static detected | static lead | static FPR | model detected (seen mechanisms) |
|---|---|---|---|---|---|---|---|
| memory_leak | 2/97 | 8 min | 0.0001 | 97/97 | 15 min | 0.0089 | 1.000 |
| latency_creep | 102/105 | 20 min | 0.0079 | 105/105 | 17 min | 0.0097 | 1.000 |
| error_ramp | 2/103 | 10 min | 0.0000 | 103/103 | 19 min | 0.0119 | 1.000 |
| cpu_contention | 24/108 | 5 min | 0.0000 | 108/108 | 14 min | 0.0085 | 0.990 |
| disk_saturation | 95/101 | 12 min | 0.0000 | 101/101 | 17 min | 0.0097 | 0.990 |

**Reading this table.** The model detects almost nothing when the mechanism was absent from training
(memory leak 2/97, error ramp 2/103, CPU contention 24/108). Latency creep and disk saturation are still detected
only because their latency/error signatures overlap with mechanisms it saw. Plain warning thresholds detect every
event of every mechanism, with ~15 to 19 minutes of lead, in this simulator. On fresh data of mechanisms it was trained on,
the model has fewer false alerts (0.22 % vs 1.02 % of healthy minutes), better minute-level recall (0.78 vs 0.70)
and a slightly longer median lead (18 vs 15 min).

**Consequence for use.** This model is a supplement, not a replacement, for threshold alerting. The repository
therefore ships static-threshold safety-net alert rules (`monitoring/alert_rules.yml`, group `static-safety-net`,
kept in sync with `training/baseline.py` by a test). Any deployment must also retrain on its own data covering
the failure mechanisms it cares about.

## 4. Canary validation## Canary study: minimum observation 20 min (300-min runs)

Healthy candidates over 150 seeds: {'promote': 150}

Faulty candidates, 20 seeds per cell; value = rollbacks / runs (a fault that starts after the decision is promoted):

| fault | starts at min 10 | starts at min 30 | starts at min 60 | starts at min 90 | starts at min 120 | starts at min 150 |
|---|---|---|---|---|---|---|
| memory_leak | 20/20 | 2/20 | 0/20 | 0/20 | 0/20 | 0/20 |
| latency_creep | 20/20 | 19/20 | 0/20 | 0/20 | 0/20 | 0/20 |
| error_ramp | 20/20 | 7/20 | 0/20 | 0/20 | 0/20 | 0/20 |
| cpu_contention | 20/20 | 0/20 | 0/20 | 0/20 | 0/20 | 0/20 |
| disk_saturation | 20/20 | 5/20 | 0/20 | 0/20 | 0/20 | 0/20 |

## Canary study: minimum observation 60 min (300-min runs)

Healthy candidates over 150 seeds: {'promote': 150}

Faulty candidates, 20 seeds per cell; value = rollbacks / runs (a fault that starts after the decision is promoted):

| fault | starts at min 10 | starts at min 30 | starts at min 60 | starts at min 90 | starts at min 120 | starts at min 150 |
|---|---|---|---|---|---|---|
| memory_leak | 20/20 | 20/20 | 20/20 | 0/20 | 0/20 | 0/20 |
| latency_creep | 20/20 | 20/20 | 20/20 | 0/20 | 0/20 | 0/20 |
| error_ramp | 20/20 | 20/20 | 20/20 | 0/20 | 0/20 | 0/20 |
| cpu_contention | 20/20 | 20/20 | 19/20 | 0/20 | 0/20 | 0/20 |
| disk_saturation | 20/20 | 20/20 | 20/20 | 0/20 | 0/20 | 0/20 |

## Canary study: minimum observation 120 min (320-min runs)

Healthy candidates over 150 seeds: {'promote': 150}

Faulty candidates, 20 seeds per cell; value = rollbacks / runs (a fault that starts after the decision is promoted):

| fault | starts at min 10 | starts at min 30 | starts at min 60 | starts at min 90 | starts at min 120 | starts at min 150 |
|---|---|---|---|---|---|---|
| memory_leak | 20/20 | 20/20 | 20/20 | 20/20 | 19/20 | 0/20 |
| latency_creep | 20/20 | 20/20 | 20/20 | 20/20 | 18/20 | 0/20 |
| error_ramp | 20/20 | 20/20 | 20/20 | 20/20 | 18/20 | 0/20 |
| cpu_contention | 20/20 | 20/20 | 20/20 | 20/20 | 11/20 | 0/20 |
| disk_saturation | 20/20 | 20/20 | 20/20 | 20/20 | 18/20 | 0/20 |

**Reading these tables.** No healthy candidate was rolled back in any setting (0 of 150 each). The canary
decides once the minimum observation period has elapsed, so it catches only faults that start before the
decision point: with the default 20 minutes, a fault starting 30 minutes after deployment is mostly promoted
(memory leak 2/20 caught, CPU contention 0/20). Longer periods extend coverage at no cost in false rollbacks, only
in time-to-promote. **Recommendation:** set `PREDICTIVEOPS_CANARY='{"min_observation_minutes": 60}'` or longer for
real use, and keep the alert policy and static rules active after promotion. The default (20) is left unchanged
so the shipped demo decides quickly; that is a deliberate trade-off, not a recommendation.

## 5. What can and cannot be claimed
* Can: the pipeline is leakage-controlled (chronological split, purge gap, validation-only calibration and
  threshold selection), reproducible, and beats static thresholds on false alerts for the mechanisms it is trained on.
* Cannot: that it predicts real production failures, that it generalises to new failure mechanisms, or that
  canary decisions are reliable beyond the observation window.
* Not measured: real data, concept drift over time, multi-service dependencies, behaviour under metric gaps in
  production, and Docker-specific behaviour (see `PROJECT_STATUS.md`).

## Reproduce
```bash
python scripts/generate_data.py --days 14 --series 4 --seed 42 --out data/generated/synthetic.csv.gz
python scripts/train.py --promote                      # reference model (sections 1)
python scripts/evaluate_robustness.py --parts seeds,holdout,canary --seeds 5 --canary-min-obs 20,60,120 \
    --markdown artifacts/reports/robustness.md         # sections 2-4; ~10 min on one CPU core
```
