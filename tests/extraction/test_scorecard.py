from __future__ import annotations

from pathlib import Path

from dcf_engine.claim import Claim
from dcf_engine.extraction.client import ExtractionResponse, TokenUsage
from dcf_engine.extraction.evaluator import (
    MATCH_THRESHOLD,
    is_grounded,
    match_claims_to_facts,
    match_score,
    normalize_numbers,
    read_json_object,
    score_extraction,
)
from dcf_engine.extraction.gold import (
    FactPeriod,
    GoldFact,
    GoldFactSet,
    MagnitudeBands,
    NumericFact,
    SourceFiling,
    load_gold_facts,
)

ROOT = Path(__file__).resolve().parents[2]
CHUNKS_DIR = ROOT / "data" / "benchmark" / "chunks"
GOLD_FACTS_PATH = ROOT / "data" / "benchmark" / "gold_facts.json"
HAIKU_RESULT_PATH = (
    ROOT
    / "data"
    / "benchmark"
    / "results"
    / "claude-haiku-4-5-20251001__20260607T081235Z.json"
)


def test_normalize_numbers_handles_currency_commas_and_percentages() -> None:
    assert 74.55 in normalize_numbers("$74.550 billion")
    assert 39589.0 in normalize_numbers("39,589")
    assert 88.0 in normalize_numbers("up 88%")


def test_match_score_separates_related_and_unrelated_claims() -> None:
    revenue_evidence = (
        "Compute & Networking revenue of $74.550 billion compared with $39.589 billion, "
        "a year-over-year increase of $34.961 billion, or 88%"
    )
    revenue_statement = (
        "Compute & Networking revenue increased 88% year over year to $74.550 billion."
    )
    fact = _fact(
        fact_id="fact-01-01",
        evidence_span=revenue_evidence,
        canonical_statement=revenue_statement,
        numeric_facts=[
            NumericFact(metric="revenue", value=74.55, unit="USD_BN", period="current"),
            NumericFact(metric="revenue", value=39.589, unit="USD_BN", period="prior"),
            NumericFact(metric="revenue", value=88, unit="PCT", period="change_pct"),
        ],
    )
    related = _claim(
        claim_id="related",
        chunk_ref="chunk-01",
        text="Compute & Networking revenue increased 88% to $74.550 billion.",
    )
    unrelated = _claim(
        claim_id="unrelated",
        chunk_ref="chunk-01",
        text="Dividend increased from $0.01 per share to $0.25 per share.",
        subject="CAPITAL_ALLOCATION",
    )

    assert match_score(related, fact) >= MATCH_THRESHOLD
    assert match_score(unrelated, fact) < MATCH_THRESHOLD


def test_is_grounded_uses_chunk_numbers_and_text_overlap() -> None:
    chunk_text = (
        "Revenue increased 88% to $74.550 billion. "
        "Blackwell demand drove data center growth."
    )

    assert is_grounded(
        _claim(claim_id="numeric", chunk_ref="chunk-1", text="Revenue increased 88%."),
        chunk_text,
    )
    assert not is_grounded(
        _claim(claim_id="hallucinated", chunk_ref="chunk-1", text="Revenue increased 999%."),
        chunk_text,
    )
    assert is_grounded(
        _claim(
            claim_id="qualitative",
            chunk_ref="chunk-1",
            text="Blackwell demand drove data center growth.",
        ),
        chunk_text,
    )


def test_magnitude_subject_and_direction_axes_are_scored_on_matched_pairs() -> None:
    gold = _gold_fact_set(
        {
            "chunk-1": [
                _fact(
                    fact_id="fact-01-01",
                    allowed_subjects=["DEMAND_SIGNAL", "FINANCIAL_HEALTH"],
                    magnitude_basis="qualitative",
                    magnitude="STRONG",
                    direction="INCREASE",
                    evidence_span="Blackwell demand drove growth",
                    canonical_statement="Blackwell demand drove growth.",
                    numeric_facts=[],
                ),
                _fact(
                    fact_id="fact-01-02",
                    allowed_subjects=["COST_SIGNAL"],
                    magnitude_basis="numeric",
                    magnitude="STRONG",
                    direction="INCREASE",
                    evidence_span="Cost increased 58%",
                    canonical_statement="Cost increased 58%.",
                    numeric_facts=[
                        NumericFact(metric="cost", value=58, unit="PCT", period="change_pct")
                    ],
                ),
            ]
        }
    )
    responses = [
        ExtractionResponse(
            chunk_id="chunk-1",
            claims=[
                _claim(
                    claim_id="qualitative-near",
                    chunk_ref="chunk-1",
                    text="Blackwell demand drove growth.",
                    subject="FINANCIAL_HEALTH",
                    magnitude="EXTREME",
                ),
                _claim(
                    claim_id="numeric-off-by-one",
                    chunk_ref="chunk-1",
                    text="Cost increased 58%.",
                    subject="COST_SIGNAL",
                    magnitude="EXTREME",
                ),
            ],
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
            latency_ms=1,
        )
    ]

    scorecard = score_extraction(
        gold=gold,
        responses=responses,
        chunk_texts={"chunk-1": "Blackwell demand drove growth. Cost increased 58%."},
    )

    assert scorecard.true_positives == 2
    assert scorecard.direction_accuracy == 1.0
    assert scorecard.subject_accuracy == 1.0
    assert scorecard.magnitude_accuracy == 0.5


def test_score_extraction_does_not_match_across_chunks() -> None:
    gold = _gold_fact_set(
        {
            "chunk-a": [
                _fact(
                    fact_id="fact-01-01",
                    evidence_span="Other income increased sharply.",
                    canonical_statement="Other income increased sharply.",
                )
            ],
            "chunk-b": [],
        }
    )
    responses = [
        ExtractionResponse(
            chunk_id="chunk-b",
            claims=[
                _claim(
                    claim_id="wrong-chunk",
                    chunk_ref="chunk-b",
                    text="Other income increased sharply.",
                    subject="FINANCIAL_HEALTH",
                    magnitude="EXTREME",
                )
            ],
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
            latency_ms=1,
        )
    ]

    scorecard = score_extraction(
        gold=gold,
        responses=responses,
        chunk_texts={
            "chunk-a": "Other income increased sharply.",
            "chunk-b": "Other income increased sharply.",
        },
    )

    assert scorecard.true_positives == 0
    assert scorecard.false_negatives == 1
    assert scorecard.coverage_recall == 0.0


def test_haiku_chunk_07_schema_failure_is_not_hidden_by_other_chunks() -> None:
    gold = load_gold_facts(GOLD_FACTS_PATH)
    responses = _saved_haiku_responses()

    scorecard = score_extraction(
        gold=gold,
        responses=responses,
        chunk_texts=_chunk_texts(),
    )

    assert len(gold.facts_by_chunk["chunk-07-investment-gains-tax"]) > 0
    assert scorecard.coverage_recall < 0.8
    assert scorecard.false_negatives >= len(
        gold.facts_by_chunk["chunk-07-investment-gains-tax"]
    )


def test_haiku_grounded_precision_is_not_limited_by_gold_claim_count() -> None:
    scorecard = score_extraction(
        gold=load_gold_facts(GOLD_FACTS_PATH),
        responses=_saved_haiku_responses(),
        chunk_texts=_chunk_texts(),
    )

    assert scorecard.total_claims == 42
    assert scorecard.grounded_precision > 0.19
    assert scorecard.redundancy_rate > 0.0


def test_scorecard_ratios_stay_in_unit_interval() -> None:
    scorecard = score_extraction(
        gold=load_gold_facts(GOLD_FACTS_PATH),
        responses=_saved_haiku_responses(),
        chunk_texts=_chunk_texts(),
    )

    ratios = [
        scorecard.coverage_recall,
        scorecard.primary_coverage_recall,
        scorecard.grounded_precision,
        scorecard.numeric_grounding_rate,
        scorecard.direction_accuracy,
        scorecard.magnitude_accuracy,
        scorecard.subject_accuracy,
        scorecard.redundancy_rate,
    ]

    assert all(0.0 <= ratio <= 1.0 for ratio in ratios)


def test_match_claims_to_facts_returns_one_to_one_assignments() -> None:
    facts = [
        _fact(
            fact_id="fact-01-01",
            evidence_span="Revenue increased 88%",
            canonical_statement="Revenue increased 88%.",
        )
    ]
    claims = [
        _claim(claim_id="a", chunk_ref="chunk-1", text="Revenue increased 88%."),
        _claim(claim_id="b", chunk_ref="chunk-1", text="Revenue increased 88%."),
    ]

    pairs = match_claims_to_facts(claims, facts)

    assert len(pairs) == 1


def _saved_haiku_responses() -> list[ExtractionResponse]:
    result = read_json_object(HAIKU_RESULT_PATH)
    response_records = result["responses"]
    if not isinstance(response_records, list):
        raise AssertionError("responses must be a list")
    responses: list[ExtractionResponse] = []
    for response in response_records:
        if not isinstance(response, dict):
            raise AssertionError("response must be an object")
        claims = response.get("claims")
        usage = response.get("usage")
        if not isinstance(claims, list) or not isinstance(usage, dict):
            raise AssertionError("response claims and usage must be objects")
        responses.append(
            ExtractionResponse(
                chunk_id=str(response["chunk_id"]),
                claims=[Claim.model_validate(claim) for claim in claims],
                usage=TokenUsage(
                    prompt_tokens=int(usage["prompt_tokens"]),
                    completion_tokens=int(usage["completion_tokens"]),
                ),
                latency_ms=int(response["latency_ms"]),
                schema_valid=bool(response["schema_valid"]),
                error=response.get("error") if isinstance(response.get("error"), str) else None,
            )
        )
    return responses


def _chunk_texts() -> dict[str, str]:
    return {path.stem: path.read_text() for path in CHUNKS_DIR.glob("*.txt")}


def _gold_fact_set(facts_by_chunk: dict[str, list[GoldFact]]) -> GoldFactSet:
    return GoldFactSet(
        schema_version=2,
        label_status="draft_pending_user_freeze",
        source_filing=SourceFiling(
            company="NVIDIA CORP",
            form="10-Q",
            accession="test",
            filing_date="2026-05-20",
            period_end="2026-04-26",
            url="https://example.com",
        ),
        magnitude_bands=MagnitudeBands.default(),
        labeling_rule="test",
        facts_by_chunk=facts_by_chunk,
    )


def _fact(
    *,
    fact_id: str,
    evidence_span: str,
    canonical_statement: str,
    allowed_subjects: list[str] | None = None,
    direction: str = "INCREASE",
    magnitude: str = "EXTREME",
    magnitude_basis: str = "qualitative",
    numeric_facts: list[NumericFact] | None = None,
) -> GoldFact:
    return GoldFact.model_validate(
        {
            "fact_id": fact_id,
            "canonical_statement": canonical_statement,
            "evidence_span": evidence_span,
            "allowed_subjects": allowed_subjects or ["FINANCIAL_HEALTH"],
            "direction": direction,
            "magnitude_qualifier": magnitude,
            "magnitude_basis": magnitude_basis,
            "acceptable_natures": ["REALIZED"],
            "period": FactPeriod(current="Q1 FY2027", prior="Q1 FY2026"),
            "numeric_facts": numeric_facts or [],
            "macro_variable": None,
            "salience": "primary",
        }
    )


def _claim(
    *,
    claim_id: str,
    chunk_ref: str,
    text: str,
    subject: str = "DEMAND_SIGNAL",
    direction: str = "INCREASE",
    magnitude: str = "STRONG",
) -> Claim:
    return Claim.model_validate(
        {
            "claim_id": claim_id,
            "claim_text": text,
            "claim_subject": subject,
            "claim_nature": "REALIZED",
            "direction": direction,
            "magnitude_qualifier": magnitude,
            "macro_variable": None,
            "instrument_type": None,
            "extraction_quality": {
                "verbatim_overlap": 1.0,
                "numeric_consistency": True,
                "temporal_consistency": True,
                "entity_consistency": True,
            },
            "source_ref": {
                "discovery_channel": "edgar_api",
                "content_source": "10-Q",
                "source_reliability": 0.95,
            },
            "chunk_ref": chunk_ref,
            "published_date": "2026-05-20",
        }
    )
