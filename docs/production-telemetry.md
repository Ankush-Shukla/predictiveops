# Production telemetry and model contract

PredictiveOps has two independent prediction scopes. They must use separate feature schemas,
training datasets, model registries, buffers and quality reports. A host model must never be
fed application request metrics, and an application model must never be fed host-wide metrics
as substitutes for missing observations.

## Host scope

One series represents one monitored host. The required one-minute metrics are:

| Model field | Prometheus source | Transformation |
|---|---|---|
| `cpu_pct` | `node_cpu_seconds_total` | `100 - 100 * avg(rate(...{mode="idle"}[5m]))` |
| `memory_pct` | `node_memory_MemAvailable_bytes`, `node_memory_MemTotal_bytes` | `100 * (1 - available / total)` |
| `disk_io_util_pct` | `node_disk_io_time_seconds_total` | `100 * rate(...[5m])`, aggregated per physical device |
| `network_rx_mbps` | `node_network_receive_bytes_total` | `rate(...[5m]) * 8 / 1e6`, physical interfaces only |
| `network_tx_mbps` | `node_network_transmit_bytes_total` | `rate(...[5m]) * 8 / 1e6`, physical interfaces only |

The host model must be trained with exactly these fields and incident labels for host resource
exhaustion. Root filesystem capacity is monitored separately as a deterministic safety alert;
it is not silently substituted for disk I/O utilization.

## Application scope

Each monitored application must expose Prometheus metrics on an HTTP scrape endpoint. The
stable application metric contract is:

- `predictiveops_app_requests_total{service,method,status}` — monotonically increasing request counter.
- `predictiveops_app_request_duration_seconds_bucket{service,le}` — request-duration histogram.
- `predictiveops_app_request_duration_seconds_count{service}` — request count for the histogram.

PredictiveOps derives request rate from the request counter, p95 latency from the histogram,
and error rate from 5xx requests divided by all requests. Container resource metrics are joined
by the configured service/container identity. Every application must have its own stable
`service` label and one-minute series identity.

Applications that do not expose this contract can still be scraped and visualized, but they are
not eligible for model inference until an explicit metric adapter is configured. Missing
measurements must remain missing; do not replace them with zero.

## Training and production eligibility

Training files are one-minute, time-ordered series with a stable `series_id`, a `data_source`
provenance field, the scope's required metric columns, and an `is_unhealthy` ground-truth label.
The training pipeline derives the forward horizon label from `is_unhealthy`. Labels must come
from reviewed incident/impact records or an explicitly documented operational definition;
unlabelled telemetry is not a supervised training dataset.

Host and application datasets are trained and promoted independently. The production API rejects
synthetic-only models and models without a passing evaluation gate report. Synthetic training
remains available for development and CI, but synthetic scores are not production predictions.

## Operational safeguards

- Exporter availability, scrape freshness and missing series are health signals, not zero-valued
  resource measurements.
- Keep threshold-based alerts active as a separate safety net.
- Remediation remains disabled and dry-run by default. Do not enable Docker socket access until
  the action path has been tested against a disposable target and reviewed.
- Model buffers and latest predictions must be persisted or explicitly treated as ephemeral;
  in-memory state is not durable across API restarts.
