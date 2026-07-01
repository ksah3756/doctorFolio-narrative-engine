from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from dcf_engine.claim import (
    SOURCE_RELIABILITY,
    Claim,
    ClaimSubject,
    ExtractionQuality,
    SourceRef,
)
from dcf_engine.extraction.client import ExtractionResponse, TokenUsage
from dcf_engine.extraction.gold import GoldFactSet, load_gold_facts
from dcf_engine.factor import FactorName, FactorState
from dcf_engine.ingestion import JsonClaimStore, SourceDocument
from dcf_engine.ingestion.pipeline import run_ingestion_pipeline
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.routing import route_claims_to_factors
from dcf_engine.validate_cycle import run_validation_cycle

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_CHUNKS_DIR = ROOT / "data" / "benchmark" / "chunks"
GOLD_FACTS_PATH = ROOT / "data" / "benchmark" / "gold_facts.json"
VALUATION_REFERENCE_PATH = ROOT / "data" / "benchmark" / "valuation_reference.json"

_STAGE: LifecycleStage = "growth"
_SEED = 20260629
_ITERATIONS = 300
_VALUATION_BAND = 0.30
_SCHEMA_INVALID_DOC_ID = "schema-invalid"
_PROVENANCE_MISMATCH_DOC_ID = "provenance-mismatch"


class _ValuationReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    value_usd: float
    source: str
    as_of_date: date
    note: str


@dataclass(frozen=True)
class _BenchmarkFixture:
    documents: list[SourceDocument]
    gold_facts: GoldFactSet
    source_ref: SourceRef
    published_date: date

    @property
    def expected_claim_count(self) -> int:
        return sum(len(facts) for facts in self.gold_facts.facts_by_chunk.values())


class _FixtureFetcher:
    def __init__(self, documents: list[SourceDocument]) -> None:
        self._documents = documents

    def fetch(self) -> Iterable[SourceDocument]:
        return self._documents


class _GoldFactReplayExtractor:
    def __init__(self, fixture: _BenchmarkFixture) -> None:
        self._fixture = fixture

    def extract_claims(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        if chunk_id == f"{_SCHEMA_INVALID_DOC_ID}-0001":
            return _response(
                chunk_id=chunk_id,
                claims=[
                    _claim(
                        claim_id="schema-invalid-claim",
                        claim_text="Schema-invalid payload mentioned Blackwell demand.",
                        subject="DEMAND_SIGNAL",
                        chunk_id=chunk_id,
                        fixture=self._fixture,
                    )
                ],
                schema_valid=False,
                error="schema validation failed for malformed fixture payload",
            )
        if chunk_id == f"{_PROVENANCE_MISMATCH_DOC_ID}-0001":
            return _response(
                chunk_id=chunk_id,
                claims=[
                    _claim(
                        claim_id="provenance-mismatch-claim",
                        claim_text="Provenance mismatch payload mentioned stronger AI demand.",
                        subject="DEMAND_SIGNAL",
                        chunk_id="wrong-chunk-ref",
                        fixture=self._fixture,
                    )
                ],
            )

        gold_key = _gold_key_for_body_chunk(chunk_id)
        if gold_key is None:
            return _response(chunk_id=chunk_id, claims=[])

        claims: list[Claim] = []
        for fact in self._fixture.gold_facts.facts_by_chunk[gold_key]:
            assert fact.evidence_span in chunk_text
            subject = fact.allowed_subjects[0]
            claims.append(
                Claim(
                    claim_id=f"gold-{fact.fact_id}",
                    claim_text=fact.canonical_statement,
                    claim_subject=subject,
                    claim_nature=fact.acceptable_natures[0],
                    direction=fact.direction,
                    magnitude_qualifier=fact.magnitude_qualifier,
                    macro_variable=(
                        fact.macro_variable if subject == "MACRO_EXPOSURE" else None
                    ),
                    extraction_quality=_quality(),
                    source_ref=self._fixture.source_ref,
                    chunk_ref=chunk_id,
                    published_date=self._fixture.published_date,
                )
            )
        return _response(chunk_id=chunk_id, claims=claims)


def test_m2_real_nvda_fixture_runs_deterministic_end_to_end(tmp_path: Path) -> None:
    fixture = _benchmark_fixture()
    store = JsonClaimStore(tmp_path / "store")
    fetcher = _FixtureFetcher(fixture.documents)
    extractor = _GoldFactReplayExtractor(fixture)

    first = run_ingestion_pipeline(fetchers=[fetcher], store=store, extractor=extractor)
    claim_count_after_first = len(store.load_all_claims())
    second = run_ingestion_pipeline(fetchers=[fetcher], store=store, extractor=extractor)
    persisted_claims = store.load_all_claims()

    assert first.documents_processed == 12
    assert first.chunks_processed == 22
    assert first.chunks_rejected == 2
    assert first.claims_saved == fixture.expected_claim_count
    assert first.claims_saved >= 20
    assert first.error_count == 2
    assert any("schema validation failed" in error.message for error in first.errors)
    assert any("claim provenance mismatch" in error.message for error in first.errors)
    assert second.documents_fetched == 12
    assert second.documents_skipped == 12
    assert second.chunks_processed == 0
    assert len(persisted_claims) == claim_count_after_first
    assert len(persisted_claims) == fixture.expected_claim_count

    claim_ids = {claim.claim_id for claim in persisted_claims}
    assert len(claim_ids) == len(persisted_claims)
    assert "schema-invalid-claim" not in claim_ids
    assert "provenance-mismatch-claim" not in claim_ids
    assert _count_by_source(persisted_claims, "10-Q") == fixture.expected_claim_count

    factors = route_claims_to_factors(persisted_claims, _STAGE)
    assert factors
    assert all(_is_valid_factor_state(name, state) for name, state in factors.items())

    report = run_validation_cycle(
        persisted_claims,
        stage=_STAGE,
        seed=_SEED,
        iterations=_ITERATIONS,
    )

    assert report.fair_value_p10_usd <= report.fair_value_median_usd <= report.fair_value_p90_usd
    assert report.fair_value_median_usd == _within_reference_band(
        report.fair_value_median_usd
    )


def _benchmark_fixture() -> _BenchmarkFixture:
    gold_facts = load_gold_facts(GOLD_FACTS_PATH)
    published_date = date.fromisoformat(gold_facts.source_filing.filing_date)
    source_ref = _source_ref("edgar_api", "10-Q")
    documents = [
        SourceDocument(
            doc_id=chunk_path.stem,
            url=gold_facts.source_filing.url,
            title=f"{gold_facts.source_filing.company} {chunk_path.stem}",
            published_date=published_date,
            source_ref=source_ref,
            raw_text=chunk_path.read_text(),
        )
        for chunk_path in sorted(BENCHMARK_CHUNKS_DIR.glob("chunk-*.txt"))
    ]
    documents.extend(_hard_reject_documents(source_ref, published_date, gold_facts))
    return _BenchmarkFixture(
        documents=documents,
        gold_facts=gold_facts,
        source_ref=source_ref,
        published_date=published_date,
    )


def _hard_reject_documents(
    source_ref: SourceRef, published_date: date, gold_facts: GoldFactSet
) -> list[SourceDocument]:
    return [
        SourceDocument(
            doc_id=_SCHEMA_INVALID_DOC_ID,
            url=f"{gold_facts.source_filing.url}#schema-invalid",
            title="NVIDIA malformed extraction fixture",
            published_date=published_date,
            source_ref=source_ref,
            raw_text="Schema-invalid payload mentioned Blackwell demand.",
        ),
        SourceDocument(
            doc_id=_PROVENANCE_MISMATCH_DOC_ID,
            url=f"{gold_facts.source_filing.url}#provenance-mismatch",
            title="NVIDIA provenance mismatch fixture",
            published_date=published_date,
            source_ref=source_ref,
            raw_text="Provenance mismatch payload mentioned stronger AI demand.",
        ),
    ]


def _gold_key_for_body_chunk(chunk_id: str) -> str | None:
    if not chunk_id.endswith("-0002"):
        return None
    gold_key = chunk_id.removesuffix("-0002")
    if gold_key not in load_gold_facts(GOLD_FACTS_PATH).facts_by_chunk:
        return None
    return gold_key


def _claim(
    *,
    claim_id: str,
    claim_text: str,
    subject: ClaimSubject,
    chunk_id: str,
    fixture: _BenchmarkFixture,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=claim_text,
        claim_subject=subject,
        claim_nature="REALIZED",
        direction="INCREASE",
        magnitude_qualifier="STRONG",
        extraction_quality=_quality(),
        source_ref=fixture.source_ref,
        chunk_ref=chunk_id,
        published_date=fixture.published_date,
    )


def _quality() -> ExtractionQuality:
    return ExtractionQuality(
        verbatim_overlap=1.0,
        numeric_consistency=True,
        temporal_consistency=True,
        entity_consistency=True,
    )


def _response(
    *,
    chunk_id: str,
    claims: list[Claim],
    schema_valid: bool = True,
    error: str | None = None,
) -> ExtractionResponse:
    return ExtractionResponse(
        chunk_id=chunk_id,
        claims=claims,
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
        latency_ms=1,
        schema_valid=schema_valid,
        error=error,
    )


def _source_ref(
    discovery_channel: Literal["edgar_api"],
    content_source: Literal["10-Q"],
) -> SourceRef:
    return SourceRef(
        discovery_channel=discovery_channel,
        content_source=content_source,
        source_reliability=SOURCE_RELIABILITY[content_source],
    )


def _count_by_source(claims: list[Claim], content_source: str) -> int:
    return sum(claim.source_ref.content_source == content_source for claim in claims)


def _is_valid_factor_state(name: str, state: FactorState) -> bool:
    valid_names: set[FactorName] = {
        "DemandStrength",
        "CompetitiveAdvantage",
        "OperatingEfficiency",
        "MacroCondition",
        "ExecutionQuality",
        "FinancialStrength",
    }
    return name in valid_names and state.name == name and -3.0 <= state.current_value <= 3.0


def _within_reference_band(value: float) -> float:
    reference = _ValuationReference.model_validate_json(
        VALUATION_REFERENCE_PATH.read_text()
    )
    lower = reference.value_usd * (1 - _VALUATION_BAND)
    upper = reference.value_usd * (1 + _VALUATION_BAND)
    assert lower <= value <= upper
    return value
