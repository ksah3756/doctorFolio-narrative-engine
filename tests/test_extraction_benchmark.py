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

ROOT = Path(__file__).resolve().parents[1]
CHUNKS_DIR = ROOT / "data" / "benchmark" / "chunks"
GOLD_PATH = ROOT / "data" / "benchmark" / "gold_facts.json"
REPLAY_PATH = ROOT / "tests" / "fixtures" / "v4-flash-replay.json"


def test_replay_benchmark_reports_scorecard_metrics() -> None:
    result = run_benchmark(chunks_dir=CHUNKS_DIR, gold_path=GOLD_PATH, replay_path=REPLAY_PATH)

    assert result.schema_validation_rate == 1.0
    assert 0.0 <= result.grounded_precision <= 1.0
    assert 0.0 <= result.coverage_recall <= 1.0
    assert 0.0 <= result.primary_coverage_recall <= 1.0
    assert 0.0 <= result.numeric_grounding_rate <= 1.0
    assert 0.0 <= result.direction_accuracy <= 1.0
    assert 0.0 <= result.magnitude_accuracy <= 1.0
    assert 0.0 <= result.subject_accuracy <= 1.0
    assert 0.0 <= result.redundancy_rate <= 1.0
    assert result.true_positives >= 0
    assert result.false_negatives >= 0
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
    assert result.cost_per_chunk_usd == 0.0011379
    assert 0.0 <= result.grounded_precision <= 1.0


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


def test_cost_per_chunk_uses_all_chunk_denominator() -> None:
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

    assert _cost_per_chunk(responses, pricing=pricing, chunk_count=2) == 0.0002


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
    assert result.grounded_precision >= 0.80
    assert result.coverage_recall >= 0.75
    assert result.cost_per_chunk_usd <= 0.01
