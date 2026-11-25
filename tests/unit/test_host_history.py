import pytest

from app.services import host_history as h


def _matrix(values):
    return {"status": "success", "data": {"result": [{"values": values}]}}


def test_pick_step_caps_points():
    for minutes in (5, 15, 60, 360, 1440):
        step = h.pick_step(minutes)
        assert step >= h.MIN_STEP_SECONDS
        assert minutes * 60 / step <= h.MAX_POINTS


def test_matrix_to_map_drops_non_finite_and_keeps_floats():
    assert h.matrix_to_map(_matrix([[1, "1.5"], [2, "NaN"], [3, "2"]])) == {1.0: 1.5, 3.0: 2.0}


def test_matrix_to_map_rejects_errors_and_ambiguity():
    with pytest.raises(ValueError):
        h.matrix_to_map({"status": "error", "error": "boom"})
    with pytest.raises(ValueError):
        h.matrix_to_map({"status": "success", "data": {"result": [{}, {}]}})


def test_empty_result_is_empty_map_not_zeros():
    assert h.matrix_to_map({"status": "success", "data": {"result": []}}) == {}


def test_merge_keeps_missing_values_missing():
    points = h.merge_points({"a": {1: 1, 2: 2}, "b": {2: 5, 3: 6}})
    assert points[0] == {"t": 1, "a": 1, "b": None}
    assert points[2] == {"t": 3, "a": None, "b": 6}
    assert h.latest_values(points, ["a", "b"]) == {"a": 2, "b": 6}


def test_coverage_counts_only_up_buckets():
    cov = h.summarize_coverage(_matrix([[3600, "1"], [7200, "0"], [10800, "1"]]), 3600)
    assert cov == {"first_sample": 3600.0, "last_sample": 10800.0, "covered_hours": 2.0}


def test_alerts_sorted_firing_first():
    payload = {"status": "success", "data": {"alerts": [
        {"labels": {"alertname": "B", "severity": "warning"}, "state": "pending", "annotations": {}},
        {"labels": {"alertname": "A"}, "state": "firing", "annotations": {"summary": "s"}},
    ]}}
    assert [a["name"] for a in h.parse_alerts(payload)] == ["A", "B"]


def test_history_queries_include_all_host_model_inputs():
    from app.core.domain import HOST_METRIC_COLUMNS
    assert set(HOST_METRIC_COLUMNS) <= set(h.HOST_HISTORY_QUERIES)
