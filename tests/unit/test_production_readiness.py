from types import SimpleNamespace

from app.api.deps import production_model_block_reason


def state(env: str, metadata: dict | None):
    predictor = None if metadata is None else SimpleNamespace(
        bundle=SimpleNamespace(metadata=metadata),
        version="test-model",
    )
    return SimpleNamespace(settings=SimpleNamespace(env=env), predictor=predictor)


def test_missing_model_is_not_ready_in_every_environment():
    assert production_model_block_reason(state("development", None)) == "no champion model loaded"
    assert production_model_block_reason(state("production", None)) == "no champion model loaded"


def test_development_allows_synthetic_model_for_local_testing():
    assert production_model_block_reason(state("development", {"data_source": "synthetic"})) is None


def test_production_rejects_synthetic_model_even_when_quality_gates_pass():
    metadata = {
        "data_source": "synthetic",
        "evaluation": {"gates": {"passed": True}},
    }
    assert "not trained exclusively" in production_model_block_reason(state("production", metadata))


def test_production_rejects_real_data_model_without_quality_report():
    assert "no passing" in production_model_block_reason(
        state("production", {"data_source": "production"})
    )


def test_production_accepts_identified_real_data_with_passing_quality_gates():
    metadata = {
        "data_source": "production",
        "evaluation": {"gates": {"passed": True}},
    }
    assert production_model_block_reason(state("production", metadata)) is None
