from __future__ import annotations

from pathlib import Path

import pytest

from dcf_engine.extraction.benchmark import run_benchmark
from dcf_engine.extraction.client import CLAUDE_HAIKU_MODEL
from dcf_engine.extraction.evaluator import evaluate_extraction, load_gold_labels

ROOT = Path(__file__).resolve().parents[1]
CHUNKS_DIR = ROOT / "data" / "benchmark" / "chunks"
GOLD_PATH = ROOT / "data" / "benchmark" / "gold.json"
REPLAY_PATH = ROOT / "tests" / "fixtures" / "v4-flash-replay.json"


def test_replay_benchmark_meets_quality_bar() -> None:
    result = run_benchmark(chunks_dir=CHUNKS_DIR, gold_path=GOLD_PATH, replay_path=REPLAY_PATH)

    assert result.schema_validation_rate == 1.0
    assert result.precision >= 0.80
    assert result.recall >= 0.75
    assert result.cost_per_chunk_usd <= 0.01
    assert result.chunk_count == 10
    assert result.model == "deepseek-v4-flash"


def test_claude_haiku_replay_benchmark_uses_haiku_model_and_pricing() -> None:
    result = run_benchmark(
        chunks_dir=CHUNKS_DIR,
        gold_path=GOLD_PATH,
        replay_path=REPLAY_PATH,
        provider="anthropic",
        model=CLAUDE_HAIKU_MODEL,
    )

    assert result.model == "claude-haiku-4-5-20251001"
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.cost_per_chunk_usd == 0.0011379


def test_gold_labels_cover_all_benchmark_chunks() -> None:
    gold = load_gold_labels(GOLD_PATH)
    chunk_ids = {path.stem for path in CHUNKS_DIR.glob("*.txt")}

    assert len(chunk_ids) == 10
    assert set(gold.claims_by_chunk) == chunk_ids


def test_evaluator_matches_claims_on_subject_direction_and_magnitude() -> None:
    metrics = evaluate_extraction(
        expected=[
            {
                "claim_id": "gold-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            },
            {
                "claim_id": "gold-2",
                "claim_subject": "COST_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "MODERATE",
            },
        ],
        actual=[
            {
                "claim_id": "actual-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            },
            {
                "claim_id": "actual-2",
                "claim_subject": "COST_SIGNAL",
                "direction": "DECREASE",
                "magnitude_qualifier": "MODERATE",
            },
        ],
    )

    assert metrics.true_positives == 1
    assert metrics.false_positives == 1
    assert metrics.false_negatives == 1
    assert metrics.precision == 0.5
    assert metrics.recall == 0.5


@pytest.mark.live
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("deepseek", "deepseek-v4-flash"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
)
def test_live_model_meets_quality_bar(provider: str, model: str) -> None:
    result = run_benchmark(chunks_dir=CHUNKS_DIR, gold_path=GOLD_PATH, provider=provider, model=model)

    assert result.schema_validation_rate == 1.0
    assert result.precision >= 0.80
    assert result.recall >= 0.75
    assert result.cost_per_chunk_usd <= 0.01
