import pandas as pd
import pytest

from app.core.exceptions import DataGenerationError
from app.models.schemas import MetricPoint
from app.services.series_buffer import SeriesBuffer
from data.synthetic.scenarios import generate_scenario

T0 = pd.Timestamp("2025-01-01T00:00:00Z")


def pt(minute: int, **kw) -> MetricPoint:
    base = dict(cpu_pct=10, memory_pct=50, disk_io_util_pct=5, network_rx_mbps=1, network_tx_mbps=1,
                request_rate_rps=10, latency_p95_ms=100, error_rate=0.01)
    base.update(kw)
    return MetricPoint(timestamp=(T0 + pd.Timedelta(minutes=minute)).to_pydatetime(), **base)


def test_buffer_accepts_contiguous_and_rejects_duplicates_and_old():
    b = SeriesBuffer(100)
    assert b.add([pt(0), pt(1), pt(2)]).accepted == 3
    out = b.add([pt(2), pt(1), pt(3)])
    assert (out.accepted, out.rejected) == (1, 2) and len(b) == 4


def test_buffer_sorts_unordered_batch():
    b = SeriesBuffer(100)
    assert b.add([pt(2), pt(0), pt(1)]).accepted == 3
    assert list(b.frame()["timestamp"].diff().dropna().unique()) == [pd.Timedelta(minutes=1)]


def test_gap_clears_history():
    b = SeriesBuffer(100)
    b.add([pt(0), pt(1), pt(2)])
    out = b.add([pt(10)])
    assert out.gap_resets == 1 and len(b) == 1


def test_buffer_is_bounded_and_handles_naive_timestamps():
    b = SeriesBuffer(5)
    b.add([pt(i) for i in range(20)])
    assert len(b) == 5
    naive = MetricPoint(timestamp="2025-01-01T00:20:00", cpu_pct=1, memory_pct=1, disk_io_util_pct=1,
                        network_rx_mbps=1, network_tx_mbps=1, request_rate_rps=1, latency_p95_ms=1,
                        error_rate=0)
    assert b.add([naive]).accepted == 1


@pytest.mark.parametrize("kind", ["memory_leak", "latency_creep", "error_ramp", "cpu_contention",
                                  "disk_saturation"])
def test_scenarios_with_fault_fail_and_have_warning_labels(kind):
    d = generate_scenario(kind, seed=3)
    assert d["is_unhealthy"].any() and d["label_failure_within_h"].sum() > 0
    assert (d["data_source"] == "synthetic").all() and len(d) == 360


def test_healthy_scenario_never_fails_even_with_spikes():
    for seed in range(5):
        d = generate_scenario("none", seed=seed, spikes=True)
        assert not d["is_unhealthy"].any()


def test_scenario_deterministic_and_validated():
    assert generate_scenario("memory_leak", seed=1).equals(generate_scenario("memory_leak", seed=1))
    for bad in (dict(fault_type="nope"), dict(fault_type="memory_leak", fault_start=10),
                dict(fault_type="memory_leak", fault_start=300, minutes=360),
                dict(fault_type="memory_leak", minutes=50)):
        kw = dict(bad)
        with pytest.raises(DataGenerationError):
            generate_scenario(kw.pop("fault_type"), **kw)
