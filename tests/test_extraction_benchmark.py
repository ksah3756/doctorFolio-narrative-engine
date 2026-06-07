from __future__ import annotations

from pathlib import Path

import pytest

from dcf_engine.extraction.benchmark import (
    Pricing,
    ProviderName,
    _cost_per_chunk,
    _latency_ms_p50,
    _schema_validation_rate,
    run_benchmark,
)
from dcf_engine.extraction.client import (
    CLAUDE_HAIKU_MODEL,
    ExtractionResponse,
    TokenUsage,
    _claims_from_content,
)
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


def test_gold_gross_margin_direction_matches_claim_text() -> None:
    gold = load_gold_labels(GOLD_PATH)

    claim = gold.claims_by_chunk["chunk-05-gross-margin"][0]

    assert "Gross margin increased" in claim.claim_text
    assert claim.direction == "INCREASE"


def test_evaluator_matches_claims_on_subject_direction_and_magnitude() -> None:
    metrics = evaluate_extraction(
        expected=[
            {
                "claim_id": "gold-1",
                "chunk_ref": "chunk-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            },
            {
                "claim_id": "gold-2",
                "chunk_ref": "chunk-2",
                "claim_subject": "COST_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "MODERATE",
            },
        ],
        actual=[
            {
                "claim_id": "actual-1",
                "chunk_ref": "chunk-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            },
            {
                "claim_id": "actual-2",
                "chunk_ref": "chunk-2",
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


def test_evaluator_does_not_match_claims_across_chunks() -> None:
    metrics = evaluate_extraction(
        expected=[
            {
                "claim_id": "gold-7",
                "chunk_ref": "chunk-7",
                "claim_subject": "FINANCIAL_HEALTH",
                "direction": "INCREASE",
                "magnitude_qualifier": "EXTREME",
            }
        ],
        actual=[
            {
                "claim_id": "actual-1",
                "chunk_ref": "chunk-1",
                "claim_subject": "FINANCIAL_HEALTH",
                "direction": "INCREASE",
                "magnitude_qualifier": "EXTREME",
            }
        ],
    )

    assert metrics.true_positives == 0
    assert metrics.false_positives == 1
    assert metrics.false_negatives == 1
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0


def test_draft_gold_coverage_can_ignore_extra_actual_claims() -> None:
    metrics = evaluate_extraction(
        expected=[
            {
                "claim_id": "gold-1",
                "chunk_ref": "chunk-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            }
        ],
        actual=[
            {
                "claim_id": "actual-1",
                "chunk_ref": "chunk-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            },
            {
                "claim_id": "actual-extra",
                "chunk_ref": "chunk-1",
                "claim_subject": "FINANCIAL_HEALTH",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            },
        ],
        penalize_extra_claims=False,
    )

    assert metrics.true_positives == 1
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


def test_draft_gold_coverage_fails_when_seed_claim_is_missing() -> None:
    metrics = evaluate_extraction(
        expected=[
            {
                "claim_id": "gold-1",
                "chunk_ref": "chunk-1",
                "claim_subject": "DEMAND_SIGNAL",
                "direction": "INCREASE",
                "magnitude_qualifier": "STRONG",
            }
        ],
        actual=[],
        penalize_extra_claims=False,
    )

    assert metrics.true_positives == 0
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 1
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0


def test_claim_parser_accepts_markdown_wrapped_json() -> None:
    claims = _claims_from_content(
        """
        Here is the extracted JSON:

        ```json
        {
          "claims": [
            {
              "claim_id": "actual-1",
              "claim_text": "Compute revenue increased.",
              "claim_subject": "DEMAND_SIGNAL",
              "claim_nature": "REALIZED",
              "direction": "INCREASE",
              "magnitude_qualifier": "STRONG",
              "macro_variable": null,
              "instrument_type": null,
              "extraction_quality": {
                "verbatim_overlap": 0.9,
                "numeric_consistency": true,
                "temporal_consistency": true,
                "entity_consistency": true
              },
              "source_ref": {
                "discovery_channel": "edgar_api",
                "content_source": "10-Q",
                "source_reliability": 0.95
              },
              "chunk_ref": "chunk-1",
              "published_date": "2026-05-20"
            }
          ]
        }
        ```
        """
    )

    assert claims[0].claim_subject == "DEMAND_SIGNAL"


def test_schema_validation_rate_counts_invalid_live_responses() -> None:
    responses = [
        ExtractionResponse(
            chunk_id="chunk-1",
            claims=[],
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
            latency_ms=0,
            schema_valid=False,
            error="ValidationError",
        ),
        ExtractionResponse(
            chunk_id="chunk-2",
            claims=[],
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
            latency_ms=0,
        ),
    ]

    assert _schema_validation_rate(responses, {"chunk-1": "text", "chunk-2": "text"}) == 0.5


def test_latency_p50_uses_only_schema_valid_responses() -> None:
    responses = [
        ExtractionResponse(
            chunk_id="chunk-1",
            claims=[],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
            latency_ms=0,
            schema_valid=False,
            error="ValidationError",
        ),
        ExtractionResponse(
            chunk_id="chunk-2",
            claims=[],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
            latency_ms=100,
        ),
        ExtractionResponse(
            chunk_id="chunk-3",
            claims=[],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
            latency_ms=300,
        ),
    ]

    assert _latency_ms_p50(responses) == 200.0


def test_cost_per_chunk_uses_schema_valid_chunk_denominator() -> None:
    responses = [
        ExtractionResponse(
            chunk_id="chunk-1",
            claims=[],
            usage=TokenUsage(prompt_tokens=100, completion_tokens=100),
            latency_ms=10,
            schema_valid=False,
            error="ValidationError",
        ),
        ExtractionResponse(
            chunk_id="chunk-2",
            claims=[],
            usage=TokenUsage(prompt_tokens=100, completion_tokens=100),
            latency_ms=20,
        ),
    ]
    pricing = Pricing(input_per_1m_tokens_usd=1.0, output_per_1m_tokens_usd=1.0)

    assert _cost_per_chunk(responses, pricing=pricing) == 0.0004


@pytest.mark.live
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("deepseek", "deepseek-v4-flash"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
)
def test_live_model_meets_quality_bar(provider: ProviderName, model: str) -> None:
    result = run_benchmark(
        chunks_dir=CHUNKS_DIR,
        gold_path=GOLD_PATH,
        provider=provider,
        model=model,
    )

    assert result.schema_validation_rate == 1.0
    assert result.precision >= 0.80
    assert result.recall >= 0.75
    assert result.cost_per_chunk_usd <= 0.01
