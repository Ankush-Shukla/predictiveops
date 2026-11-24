import numpy as np
import pandas as pd
import pytest

from app.core.domain import HOST_METRIC_COLUMNS, METRIC_COLUMNS
from app.core.exceptions import DataValidationError
from app.features.builder import FeatureBuilder, FeatureConfig
from data.synthetic.generator import GeneratorConfig, generate_dataset


@pytest.fixture(scope="module")
def df():
    return generate_dataset(GeneratorConfig(days=2, n_series=2, seed=5))


def test_names_unique_ordered_and_schema_stable():
    a, b = FeatureBuilder(), FeatureBuilder()
    assert len(set(a.feature_names)) == len(a.feature_names) == 152
    assert a.feature_names[0] == f"{METRIC_COLUMNS[0]}__raw"
    assert a.schema.schema_hash == b.schema.schema_hash
    other = FeatureBuilder(FeatureConfig(windows=(5, 10)))
    assert other.schema.schema_hash != a.schema.schema_hash


def test_transform_shape_order_and_warmup(df):
    fb = FeatureBuilder()
    f = fb.transform(df)
    assert list(f.columns) == fb.feature_names and f.index.equals(df.index)
    for _, g in df.groupby("series_id"):
        sub = f.loc[g.index]
        assert sub.iloc[: fb.config.required_history - 1].isna().any(axis=1).all()
        assert sub.iloc[fb.config.required_history - 1 :].notna().all(axis=None)


def test_feature_values_are_correct(df):
    fb = FeatureBuilder()
    g = df[df.series_id == "svc-00"].reset_index(drop=True)
    f = fb.transform(g)
    t = 100
    x = g["cpu_pct"].to_numpy()
    assert f.loc[t, "cpu_pct__raw"] == x[t]
    assert f.loc[t, "cpu_pct__lag_5"] == x[t - 5]
    assert np.isclose(f.loc[t, "cpu_pct__roc_5"], (x[t] - x[t - 5]) / 5)
    assert np.isclose(f.loc[t, "cpu_pct__mean_15"], x[t - 14 : t + 1].mean())
    assert np.isclose(f.loc[t, "cpu_pct__std_15"], x[t - 14 : t + 1].std())
    assert np.isclose(f.loc[t, "cpu_pct__max_30"], x[t - 29 : t + 1].max())
    assert np.isclose(f.loc[t, "cpu_pct__slope_30"], np.polyfit(np.arange(30), x[t - 29 : t + 1], 1)[0])


def test_no_lookahead(df):
    """Changing any FUTURE sample must not change features at time t."""
    fb = FeatureBuilder()
    g = df[df.series_id == "svc-00"].reset_index(drop=True)
    t = 300
    base = fb.transform(g).loc[:t]
    g2 = g.copy()
    for m in METRIC_COLUMNS:
        g2.loc[t + 1 :, m] = g2.loc[t + 1 :, m] * 3 + 7
    pd.testing.assert_frame_equal(base, fb.transform(g2).loc[:t])


def test_series_do_not_bleed_into_each_other(df):
    fb = FeatureBuilder()
    both = fb.transform(df)
    alone = fb.transform(df[df.series_id == "svc-01"].reset_index(drop=True))
    got = both.loc[df.series_id == "svc-01"].reset_index(drop=True)
    pd.testing.assert_frame_equal(got, alone)


def test_training_inference_parity(df):
    """transform_last on a truncated window == batch features at the same row."""
    fb = FeatureBuilder()
    g = df[df.series_id == "svc-00"].reset_index(drop=True)
    batch = fb.transform(g)
    for t in (60, 400, 1500):
        window = g.iloc[max(0, t - 120) : t + 1]
        last = fb.transform_last(window)
        np.testing.assert_allclose(last.to_numpy()[0], batch.loc[t].to_numpy(), rtol=1e-7, atol=1e-7)
        assert list(last.columns) == fb.feature_names



def test_host_scope_features_do_not_require_application_metrics(df):
    metrics = ("timestamp", "series_id", *HOST_METRIC_COLUMNS)
    host_only = df[list(metrics)].copy()
    fb = FeatureBuilder(FeatureConfig(metrics=HOST_METRIC_COLUMNS))
    features = fb.transform(host_only)
    assert fb.schema.version == "2"
    assert features.columns.tolist() == fb.feature_names
    assert all(name.startswith(tuple(f"{m}__" for m in HOST_METRIC_COLUMNS))
               for name in features.columns)
    assert features.iloc[-1].notna().all()


def test_input_validation(df):
    fb = FeatureBuilder()
    g = df[df.series_id == "svc-00"].reset_index(drop=True)
    with pytest.raises(DataValidationError, match="history"):
        fb.transform_last(g.iloc[:10])
    with pytest.raises(DataValidationError, match="regular"):
        fb.transform(g.drop(index=[5, 6]).reset_index(drop=True))
    with pytest.raises(DataValidationError, match="missing"):
        fb.transform(g.drop(columns=["cpu_pct"]))
