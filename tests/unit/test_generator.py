import dataclasses

import numpy as np
import pytest

from app.core.domain import METRIC_BOUNDS, METRIC_COLUMNS
from app.core.exceptions import DataGenerationError
from app.models.schemas import validate_metrics_frame
from data.synthetic.generator import (
    FAULT_TYPES,
    GeneratorConfig,
    add_labels,
    dataset_summary,
    generate_dataset,
    load_dataset,
    write_dataset,
)

SMALL = GeneratorConfig(days=4, n_series=2, seed=7)


@pytest.fixture(scope="module")
def df():
    return generate_dataset(SMALL)


def test_shape_columns_and_source(df):
    assert len(df) == 4 * 1440 * 2
    for c in (*METRIC_COLUMNS, "timestamp", "series_id", "is_unhealthy", "fault_type",
              "episode_id", "label_failure_within_h", "label_valid", "data_source"):
        assert c in df.columns
    assert (df["data_source"] == "synthetic").all()
    validate_metrics_frame(df)
    for c, (lo, hi) in METRIC_BOUNDS.items():
        assert df[c].between(lo, hi).all()


def test_time_ordered_regular_per_series(df):
    for _, g in df.groupby("series_id"):
        d = g["timestamp"].diff().dropna()
        assert (d == np.timedelta64(60, "s")).all()


def test_deterministic_and_seed_sensitive(df):
    again = generate_dataset(SMALL)
    assert df.equals(again)
    other = generate_dataset(dataclasses.replace(SMALL, seed=8))
    assert not df["cpu_pct"].equals(other["cpu_pct"])


def test_series_are_independent(df):
    a, b = (g["cpu_pct"].to_numpy() for _, g in df.groupby("series_id"))
    assert not np.allclose(a, b)


def test_has_both_classes_and_reasonable_imbalance(df):
    s = dataset_summary(df)
    assert s["fault_episodes"] > 0
    assert 0.005 < s["positive_rate_trainable"] < 0.25


def test_labels_match_bruteforce(df):
    h = SMALL.horizon_minutes
    g = df[df["series_id"] == "svc-00"].reset_index(drop=True)
    u = g["is_unhealthy"].to_numpy()
    for t in np.random.default_rng(0).integers(0, len(g) - h - 1, 400):
        expected = int((not u[t]) and u[t + 1 : t + h + 1].any())
        assert g.loc[t, "label_failure_within_h"] == expected
    assert not g["label_valid"].iloc[-h:].any()
    assert g["label_valid"].iloc[: -h].all()


def test_positive_labels_precede_an_unhealthy_onset(df):
    h = SMALL.horizon_minutes
    for _, g in df.groupby("series_id"):
        g = g.reset_index(drop=True)
        u = g["is_unhealthy"].to_numpy()
        for t in g.index[g["label_failure_within_h"] == 1][:200]:
            assert not u[t] and u[t + 1 : t + h + 1].any()


def test_already_unhealthy_rows_have_label_zero(df):
    assert (df.loc[df["is_unhealthy"], "label_failure_within_h"] == 0).all()


def test_spikes_alone_never_cause_failure():
    cfg = GeneratorConfig(days=6, n_series=2, seed=3, fault_rate_per_day=0, spike_rate_per_day=30)
    d = generate_dataset(cfg)
    assert not d["is_unhealthy"].any()
    assert d["label_failure_within_h"].sum() == 0


@pytest.mark.parametrize("kind", FAULT_TYPES)
def test_every_fault_type_produces_failures_with_warning_window(kind):
    cfg = GeneratorConfig(days=5, n_series=1, seed=11, fault_types=(kind,), sudden_fraction=0.0,
                          spike_rate_per_day=0)
    d = generate_dataset(cfg)
    assert d["is_unhealthy"].any()
    assert (d.loc[d["fault_type"] != "none", "fault_type"] == kind).all()
    assert d["label_failure_within_h"].sum() > 0


def test_add_labels_edge_horizon():
    import pandas as pd

    u = np.zeros(100, dtype=bool)
    u[50:60] = True
    out = add_labels(pd.DataFrame({"is_unhealthy": u}), 20)
    assert out["label_failure_within_h"].iloc[30:50].eq(1).all()
    assert out["label_failure_within_h"].iloc[:30].eq(0).all()
    assert out["label_failure_within_h"].iloc[50:].eq(0).all()


def test_invalid_config_rejected():
    for bad in (dict(horizon_minutes=10), dict(days=0), dict(fault_types=("nope",)),
                dict(spike_rate_per_day=-1), dict(sudden_fraction=2)):
        with pytest.raises(DataGenerationError):
            generate_dataset(GeneratorConfig(**bad))


def test_write_and_load_roundtrip(tmp_path, df):
    import json

    p = write_dataset(df, tmp_path / "x.csv.gz", SMALL)
    meta = json.loads((tmp_path / "x.meta.json").read_text())
    assert meta["data_source"] == "synthetic" and "SYNTHETIC" in meta["warning"]
    assert len(meta["sha256"]) == 64
    back = load_dataset(p)
    assert len(back) == len(df)
    assert np.allclose(back["cpu_pct"], df["cpu_pct"])
