# Live host resource forecast

## What it does

`GET /v1/live-forecast` reads the actual one-minute host telemetry buffer populated by the production Prometheus poller. It estimates whether CPU utilization, memory utilization, or disk-I/O utilization is on course to cross an operational guardrail in the next 15 or 30 minutes.

The endpoint does not require a production champion model or locally labelled incidents. It is available while the supervised production models remain unqualified.

## Forecast method

- Requires 30 contiguous one-minute host samples.
- Uses the latest 16 observations for each resource.
- Estimates the trend with a Theil-Sen median pairwise slope, which is less sensitive to isolated scrape spikes than ordinary least squares.
- Projects the latest value 15 and 30 minutes forward, clamped to the metric's 0-100% range.
- Reports time to the warning and critical guardrails when the current trend is rising.

Current guardrails:

| Resource | Warning | Critical |
|---|---:|---:|
| CPU utilization | 85% | 95% |
| Memory utilization | 85% | 95% |
| Disk I/O utilization | 85% | 95% |

The API returns `normal`, `warning`, or `critical`, per-resource forecasts, trend direction, and source freshness. A source older than 180 seconds is marked stale.

## Important interpretation

This is a **live resource-capacity trend forecast**, not a calibrated probability that the host or an application will fail. The pressure index is a normalized guardrail-pressure indicator, not a probability. It does not replace the supervised HGB model, static alert rules, or application-level health checks. It does not trigger remediation.

A host resource forecast cannot infer application request latency, HTTP error rate, database saturation, or a container-specific failure without those measurements. Per-application failure prediction still requires the application's Prometheus metrics contract and a scope-qualified model.

## External dataset strategy

The [Server Machine Dataset (SMD)](https://github.com/NetManAIOps/OmniAnomaly) is a useful independent anomaly-detection benchmark: 28 machines, 38 telemetry dimensions, one-minute samples, and expert-labelled anomalies in the test split. The dimensions are anonymized, so they cannot be mapped honestly to this host's CPU, memory, disk and network fields. SMD can validate a separate domain-agnostic anomaly detector, but it must not be relabelled as local host incidents or used as a drop-in supervised training set for the current feature schema.

The live endpoint therefore uses the actual host's current Prometheus telemetry. SMD-based anomaly detection can be evaluated as a separate shadow signal, with its benchmark metrics and domain-transfer limitations reported independently.
