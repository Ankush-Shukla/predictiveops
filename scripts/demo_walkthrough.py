#!/usr/bin/env python
"""Narrated end-to-end demo of PredictiveOps against a running API (all data SYNTHETIC).

    uvicorn app.api.main:app_factory --factory --port 8000      # in another terminal (needs a champion model)
    python scripts/demo_walkthrough.py --base-url http://localhost:8000 [--api-key ...]

Scenarios: healthy baseline, memory-leak forecast, live ingestion with alerts + audit trail, policy preview,
canary rollback (dry-run), canary promotion, and the canary's documented blind spot (late regression).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _post(c: httpx.Client, path: str, body: dict) -> dict:
    r = c.post(path, json=body)
    if r.status_code != 200:
        raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:200]}")
    return r.json()


def run_walkthrough(c: httpx.Client, out: Callable[[str], None] = print) -> dict:
    """Run every scenario; returns a machine-readable summary (also used by the tests)."""
    s: dict = {}
    out("== 1. The model ==")
    m = c.get("/v1/model").json()
    out(f"Model {m['version']} ({m['model_type']}), trained on {m['data_source'].upper()} data; "
        f"alert threshold {m['threshold']:.0%}, horizon {m['horizon_minutes']} min.")
    s["model_version"] = m["version"]

    out("\n== 2. Healthy service (with harmless spikes) ==")
    h = _post(c, "/v1/simulate", {"fault_type": "none", "seed": 3})
    out(f"No failure occurs. Raw model alert minutes: {h['summary']['alert_minutes']}; "
        f"policy alert minutes (3 sustained needed): {h['policy_summary']['alert_minutes']}.")
    s["healthy"] = {"raw_alert_minutes": h["summary"]["alert_minutes"],
                    "policy_alert_minutes": h["policy_summary"]["alert_minutes"]}

    out("\n== 3. Memory leak: forecast before failure ==")
    leak = _post(c, "/v1/simulate", {"fault_type": "memory_leak", "seed": 42})
    sm, ps = leak["summary"], leak["policy_summary"]
    out(f"Service fails at minute {sm['failure_start_minute']}. "
        f"Raw lead time: {sm['lead_time_minutes']} min; policy lead time: {ps['lead_time_minutes']} min; "
        f"dry-run restart planned {ps['would_remediate_lead_minutes']} min before failure.")
    s["leak"] = {"lead": sm["lead_time_minutes"], "policy_lead": ps["lead_time_minutes"],
                 "would_remediate_lead": ps["would_remediate_lead_minutes"]}

    out("\n== 4. Live ingestion: the same scenario through the real pipeline ==")
    series = f"walkthrough-{int(time.time())}"
    names, n = list(leak["metrics"]), leak["minutes"]
    start = int(time.time() // 60) - n
    accepted = scored = 0
    for lo in range(0, n, 60):
        samples = [{"timestamp": time.strftime("%Y-%m-%dT%H:%M:00+00:00", time.gmtime((start + i) * 60)),
                    **{k: leak["metrics"][k][i] for k in names}} for i in range(lo, min(lo + 60, n))]
        r = _post(c, "/v1/metrics/ingest", {"series_id": series, "samples": samples})
        accepted, scored = accepted + r["accepted"], scored + r["scored_samples"]
    alerts = c.get("/v1/alerts", params={"series_id": series}).json()
    audit = c.get("/v1/audit", params={"series_id": series}).json()
    out(f"Ingested {accepted} samples, scored {scored} minutes. Alerts: "
        f"{[a['level'] for a in reversed(alerts)]}. Remediation decisions: "
        f"{[a['outcome'] for a in reversed(audit)] or 'none'}.")
    out("(Remediation is disabled by default, so a blocked outcome here is the safe, expected result.)")
    s["live"] = {"accepted": accepted, "scored": scored, "alerts": [a["level"] for a in alerts],
                 "audit": [a["outcome"] for a in audit]}

    out("\n== 5. Alert policy preview (no side effects) ==")
    pv = _post_list(c, "/v1/policy/evaluate", {"probabilities": [0.05, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9],
                                              "threshold": 0.2})
    out("Probabilities 5%, then 90% x6 -> levels: " + ", ".join(d["level"] for d in pv))
    s["policy_levels"] = [d["level"] for d in pv]

    out("\n== 6. Canary: bad release vs. healthy release ==")
    bad = _post(c, "/v1/canary/simulate", {"candidate_fault": "memory_leak", "seed": 5})
    good = _post(c, "/v1/canary/simulate", {"candidate_fault": "none", "seed": 5})
    b, g = bad["final"], good["final"]
    out(f"Leaky release -> {b['decision']} after {b['observed_minutes']} observed minutes "
        f"(rollback outcome: {b['rollback_outcome']}). Healthy release -> {g['decision']}.")
    s["canary"] = {"bad": bad["final"]["decision"], "good": good["final"]["decision"],
                   "rollback_outcome": bad["final"]["rollback_outcome"]}

    out("\n== 7. Canary blind spot: a regression that starts after the decision ==")
    late = _post(c, "/v1/canary/simulate", {"candidate_fault": "memory_leak", "fault_start": 120,
                                            "minutes": 300, "seed": 5})
    fail = next((i for i, u in enumerate(late["candidate_unhealthy"]) if u), None)
    lf = late["final"]
    out(f"Decision: {lf['decision']} at minute {lf['minute']}; the release actually fails at minute {fail}. "
        "Observation windows bound what a canary can catch (see docs/evaluation.md).")
    s["late"] = {"decision": late["final"]["decision"], "decision_minute": late["final"]["minute"],
                 "fails_at": fail}

    out("\n== Limitations to state out loud ==")
    out("* Synthetic data only: nothing here shows the model works on real systems.\n"
        "* The model misses fault mechanisms absent from training; keep static-threshold alerts "
        "as a safety net.\n"
        "* Real remediation is off by default, dry-run by default, and unverified against a real "
        "Docker daemon.")
    return s


def _post_list(c: httpx.Client, path: str, body: dict) -> list:
    r = c.post(path, json=body)
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--api-key", default=None, help="X-API-Key, if the API is protected")
    a = ap.parse_args(argv)
    headers = {"X-API-Key": a.api_key} if a.api_key else {}
    with httpx.Client(base_url=a.base_url, headers=headers, timeout=60) as c:
        try:
            run_walkthrough(c)
        except (httpx.HTTPError, RuntimeError) as exc:
            print(f"walkthrough failed: {exc}\nIs the API running with a champion model? "
                  "(python scripts/train.py --promote)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
