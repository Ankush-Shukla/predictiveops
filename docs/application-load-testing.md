# Application HTTP load testing

The dedicated `load-generator` container runs bounded HTTP tests. API-to-generator requests require a shared secret that is not exposed on a published port. Set `PREDICTIVEOPS_LOAD_GENERATOR_TOKEN` in `.env` (generate one with `openssl rand -hex 32`) before starting the stack. The API discovers target containers from Docker inventory and resolves a private IPv4 address on a network shared with the generator. Arbitrary URLs and hostnames are not accepted.

- Only running containers in the `predictiveops` Compose project are eligible.
- Ports must be declared by the target image (API port 8000 is explicitly supported).
- Limits: concurrency 1–100, request rate 1–100/second, duration 5–300 seconds.
- Generator resource limits: 0.5 CPU, 256 MiB RAM, 64 PIDs; read-only filesystem; no Docker socket; all Linux capabilities dropped.
- One test at a time; dashboard supports cancellation.
- Results include throughput, error rate, response-code distribution and p50/p95/p99/mean latency.
- Per-run warning/critical thresholds export Prometheus gauges and raise warning/critical capacity alerts.

These alerts describe a controlled load-test result, not proof of a production incident. Repeat tests under representative traffic and compare results with service-level objectives.

Deploy with:
```bash
docker compose build api frontend load-generator
docker compose up -d --no-deps load-generator api frontend
```
Prometheus now uses the tracked `prometheus/prometheus.yml` with environment expansion enabled.
