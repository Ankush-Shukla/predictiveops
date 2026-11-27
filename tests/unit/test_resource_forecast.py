from app.services.resource_forecast import project_resource_points


def samples(count=30, *, cpu_start=20.0, memory_start=4.0):
    return [
        {
            "t": 1_000_000 + index * 60,
            "cpu_pct": cpu_start + index * 0.5,
            "memory_gib": memory_start + index * 0.02,
        }
        for index in range(count)
    ]


def test_projects_twenty_minutes_with_future_timestamps_and_anchor():
    points = samples()
    result = project_resource_points(
        points, ["cpu_pct", "memory_gib"], bounded_metrics={"cpu_pct"}
    )

    assert result["status"] == "ready"
    assert result["horizon_minutes"] == 20
    assert len(result["forecast_points"]) == 21
    assert result["forecast_points"][0]["t"] == points[-1]["t"]
    assert result["forecast_points"][-1]["t"] == points[-1]["t"] + 20 * 60
    assert result["forecast_points"][0]["cpu_pct_forecast"] == points[-1]["cpu_pct"]
    assert result["forecast_points"][-1]["cpu_pct_forecast"] > points[-1]["cpu_pct"]


def test_warms_up_when_thirty_samples_are_not_available():
    result = project_resource_points(samples(29), ["cpu_pct", "memory_gib"])
    assert result["status"] == "warming_up"
    assert result["forecast_points"] == []


def test_rejects_discontinuous_sample_clock():
    points = samples()
    points[-1]["t"] += 30
    result = project_resource_points(points, ["cpu_pct", "memory_gib"])
    assert result["status"] == "warming_up"
    assert result["forecast_points"] == []


def test_percentage_forecast_is_bounded_to_one_hundred():
    points = samples(cpu_start=85.0)
    result = project_resource_points(points, ["cpu_pct"], bounded_metrics={"cpu_pct"})
    assert result["forecast_points"][-1]["cpu_pct_forecast"] == 100.0
