import numpy as np
import pandas as pd
import pytest

from app.models.predictor import Predictor
from app.services.canary_models import CanaryConfig
from data.synthetic.generator import GeneratorConfig, generate_dataset
from training.evaluate import event_metrics, event_records, summarize_events
from training.gates import GateConfig
from training.robustness import canary_study, evaluate_bundle, holdout_fault, seed_variability
from training.train import TrainConfig, run_training


def two_episode_segment():
    n = 400
    u = np.zeros(n, dtype=bool)
    ft = np.full(n, "none", dtype=object)
    ep = np.full(n, -1)
    u[150:170] = True                        # onset at 150, gradual episode (rows 90..170 = 80 min)
    ft[90:170], ep[90:170] = "memory_leak", 1
    u[320:330] = True                        # onset at 320, sudden episode (rows 310..330 = 20 min)
    ft[310:330], ep[310:330] = "error_ramp", 2
    seg = pd.DataFrame({"series_id": "a", "is_unhealthy": u, "fault_type": ft, "episode_id": ep,
                        "timestamp": pd.date_range("2025-01-01", periods=n, freq="min"),
                        "label_failure_within_h": 0})
    return seg


def test_event_records_breakdown_by_fault_and_speed():
    seg = two_episode_segment()
    pred = pd.Series(False, index=seg.index)
    pred[135] = True  # alert 15 min before the gradual memory-leak failure; nothing for the sudden one
    pred = pred[~seg.is_unhealthy]
    recs = event_records(seg, pred, 20)
    assert [r["fault_type"] for r in recs] == ["memory_leak", "error_ramp"]
    assert [r["speed"] for r in recs] == ["gradual", "sudden"]
    by_fault = summarize_events(recs, "fault_type")
    leak = by_fault["memory_leak"]
    assert leak["detected_events"] == 1 and leak["median_lead_time_min"] == 15
    assert by_fault["error_ramp"]["detected_events"] == 0
    by_speed = summarize_events(recs, "speed")
    assert by_speed["gradual"]["event_recall"] == 1.0 and by_speed["sudden"]["event_recall"] == 0.0
    overall = event_metrics(seg, pred, 20)
    assert overall["failure_events"] == 2 and overall["detected_events"] == 1
    assert event_records(seg.drop(columns=["fault_type", "episode_id"]), pred, 20)[0].keys() >= {"detected"}


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    df = generate_dataset(GeneratorConfig(days=8, n_series=3, seed=21))
    res = run_training(df, TrainConfig(model_type="logreg", model_root=tmp_path_factory.mktemp("m"),
                                       use_mlflow=False, max_fpr_for_threshold=0.2,
                                       gates=GateConfig(min_f1=0.0)))
    assert "memory_leak" in res.report["event_by_fault"] or res.report["event_by_fault"]
    return res, Predictor(res.bundle, res.version)


def test_training_report_has_breakdowns(small):
    r = small[0].report
    assert set(r["event_by_fault"]) <= {"memory_leak", "latency_creep", "error_ramp", "cpu_contention",
                                        "disk_saturation"}
    assert set(r["event_by_speed"]) <= {"gradual", "sudden"}
    total = sum(v["failure_events"] for v in r["event_by_fault"].values())
    assert total == r["event"]["failure_events"]
    assert small[0].bundle.metadata["evaluation"]["event_by_fault"] == r["event_by_fault"]


def test_evaluate_bundle_scores_whole_dataset_with_baseline(small):
    res, _ = small
    fresh = generate_dataset(GeneratorConfig(days=4, n_series=2, seed=77))
    m = evaluate_bundle(fresh, res.bundle)
    assert m["positives"] > 0 and 0 <= m["fpr"] <= 1
    assert set(m["baseline"]) >= {"recall", "fpr", "event_recall"}
    assert m["failure_events"] > 0


def test_seed_variability_and_holdout_and_canary_study_structure(small):
    sv = seed_variability([1, 2], model_type="logreg", days=8, n_series=3)
    assert len(sv["runs"]) == 2 and set(sv["summary"]["f1"]) == {"mean", "std", "min", "max"}
    ho = holdout_fault("memory_leak", model_type="logreg", days=8, n_series=3, test_days=4)
    assert ho["held_out"] == "memory_leak" and "memory_leak" not in ho["trained_on"]
    assert ho["unseen"]["failure_events"] > 0 and ho["seen"]["failure_events"] > 0
    cs = canary_study(small[1], CanaryConfig(), healthy_seeds=3, seeds_per_cell=2, fault_starts=(10,),
                      minutes=300)
    assert sum(cs["healthy_decisions"].values()) == 3
    assert set(cs["grid"]["memory_leak"]["10"]) == {"decisions", "median_rollback_minute"}
