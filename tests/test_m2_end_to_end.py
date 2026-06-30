from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from dcf_engine.claim import (
    SOURCE_RELIABILITY,
    Claim,
    ClaimDirection,
    ClaimNature,
    ClaimSubject,
    ExtractionQuality,
    MagnitudeQualifier,
    SourceRef,
)
from dcf_engine.extraction.client import ExtractionResponse, TokenUsage
from dcf_engine.factor import FactorName, FactorState
from dcf_engine.ingestion import JsonClaimStore, SourceDocument
from dcf_engine.ingestion.pipeline import run_ingestion_pipeline
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.routing import route_claims_to_factors
from dcf_engine.validate_cycle import NVDA_DAMODARAN_REF_USD, run_validation_cycle

_STAGE: LifecycleStage = "growth"
_SEED = 20260629
_ITERATIONS = 300
_VALUATION_BAND = 0.30
_EDGAR_DATE = date(2026, 5, 28)
_TRANSCRIPT_DATE = date(2026, 5, 29)


@dataclass(frozen=True)
class _ClaimSpec:
    suffix: str
    text: str
    subject: ClaimSubject
    nature: ClaimNature
    direction: ClaimDirection
    magnitude: MagnitudeQualifier


class _FixtureFetcher:
    def __init__(self, documents: list[SourceDocument]) -> None:
        self._documents = documents

    def fetch(self) -> Iterable[SourceDocument]:
        return self._documents


class _DeterministicExtractor:
    def extract_claims(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        if chunk_id == "nvda-8k-20260528-0001":
            return _response(
                chunk_id=chunk_id,
                claims=_claims(
                    prefix="8k",
                    specs=_eight_k_specs(),
                    chunk_id=chunk_id,
                    source_ref=_source_ref("edgar_api", "8-K"),
                    published_date=_EDGAR_DATE,
                ),
            )
        if chunk_id == "nvda-transcript-20260529-0001":
            return _response(
                chunk_id=chunk_id,
                claims=_claims(
                    prefix="call",
                    specs=_transcript_specs(),
                    chunk_id=chunk_id,
                    source_ref=_source_ref("direct", "earnings_call"),
                    published_date=_TRANSCRIPT_DATE,
                ),
            )
        if chunk_id == "nvda-schema-invalid-0001":
            return _response(
                chunk_id=chunk_id,
                claims=[
                    _claim(
                        claim_id="schema-invalid-claim",
                        spec=_ClaimSpec(
                            suffix="schema-invalid",
                            text="Schema-invalid payload mentioned Blackwell demand.",
                            subject="DEMAND_SIGNAL",
                            nature="REALIZED",
                            direction="INCREASE",
                            magnitude="STRONG",
                        ),
                        chunk_id=chunk_id,
                        source_ref=_source_ref("edgar_api", "8-K"),
                        published_date=_EDGAR_DATE,
                    )
                ],
                schema_valid=False,
                error="schema validation failed for malformed fixture payload",
            )
        if chunk_id == "nvda-provenance-mismatch-0001":
            return _response(
                chunk_id=chunk_id,
                claims=[
                    _claim(
                        claim_id="provenance-mismatch-claim",
                        spec=_ClaimSpec(
                            suffix="bad-provenance",
                            text="Provenance mismatch payload mentioned stronger AI demand.",
                            subject="DEMAND_SIGNAL",
                            nature="REALIZED",
                            direction="INCREASE",
                            magnitude="STRONG",
                        ),
                        chunk_id="other-source-0001",
                        source_ref=_source_ref("edgar_api", "8-K"),
                        published_date=_EDGAR_DATE,
                    )
                ],
            )
        raise AssertionError(f"unexpected chunk_id: {chunk_id}; text={chunk_text!r}")


def test_m2_real_nvda_fixture_runs_deterministic_end_to_end(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path / "store")
    fetcher = _FixtureFetcher(_source_documents())
    extractor = _DeterministicExtractor()

    first = run_ingestion_pipeline(fetchers=[fetcher], store=store, extractor=extractor)
    claim_count_after_first = len(store.load_all_claims())
    second = run_ingestion_pipeline(fetchers=[fetcher], store=store, extractor=extractor)
    persisted_claims = store.load_all_claims()

    assert first.documents_processed == 4
    assert first.chunks_processed == 4
    assert first.chunks_rejected == 2
    assert first.claims_saved >= 27
    assert first.error_count == 2
    assert any("schema validation failed" in error.message for error in first.errors)
    assert any("claim provenance mismatch" in error.message for error in first.errors)
    assert second.documents_fetched == 4
    assert second.documents_skipped == 4
    assert second.chunks_processed == 0
    assert len(persisted_claims) == claim_count_after_first

    claim_ids = {claim.claim_id for claim in persisted_claims}
    assert len(claim_ids) == len(persisted_claims)
    assert "schema-invalid-claim" not in claim_ids
    assert "provenance-mismatch-claim" not in claim_ids
    assert _count_by_source(persisted_claims, "8-K") >= 5
    assert _count_by_source(persisted_claims, "earnings_call") >= 20

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


def _source_documents() -> list[SourceDocument]:
    return [
        _document(
            doc_id="nvda-8k-20260528",
            title="NVIDIA 8-K fiscal first quarter results",
            published_date=_EDGAR_DATE,
            source_ref=_source_ref("edgar_api", "8-K"),
            raw_text=" ".join(spec.text for spec in _eight_k_specs()),
        ),
        _document(
            doc_id="nvda-transcript-20260529",
            title="NVIDIA fiscal first quarter earnings call transcript",
            published_date=_TRANSCRIPT_DATE,
            source_ref=_source_ref("direct", "earnings_call"),
            raw_text=" ".join(spec.text for spec in _transcript_specs()),
        ),
        _document(
            doc_id="nvda-schema-invalid",
            title="NVIDIA malformed extraction fixture",
            published_date=_EDGAR_DATE,
            source_ref=_source_ref("edgar_api", "8-K"),
            raw_text="Schema-invalid payload mentioned Blackwell demand.",
        ),
        _document(
            doc_id="nvda-provenance-mismatch",
            title="NVIDIA provenance mismatch fixture",
            published_date=_EDGAR_DATE,
            source_ref=_source_ref("edgar_api", "8-K"),
            raw_text="Provenance mismatch payload mentioned stronger AI demand.",
        ),
    ]


def _document(
    *,
    doc_id: str,
    title: str,
    published_date: date,
    source_ref: SourceRef,
    raw_text: str,
) -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        url=f"https://example.test/{doc_id}",
        title=title,
        published_date=published_date,
        source_ref=source_ref,
        raw_text=raw_text,
    )


def _eight_k_specs() -> list[_ClaimSpec]:
    return [
        _ClaimSpec(
            "data-center-revenue",
            "Data Center revenue increased sharply as accelerated computing demand expanded.",
            "DEMAND_SIGNAL",
            "REALIZED",
            "INCREASE",
            "EXTREME",
        ),
        _ClaimSpec(
            "blackwell-production",
            "Blackwell production ramped to support large cloud customer deployments.",
            "SUPPLY_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "gross-margin",
            "Gross margin increased as mix shifted toward higher-value AI systems.",
            "PRICING_SIGNAL",
            "REALIZED",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "operating-expenses",
            "Operating expenses increased with engineering development and compute infrastructure.",
            "COST_SIGNAL",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "repurchase",
            "The board approved additional share repurchase capacity after strong cash generation.",
            "CAPITAL_ALLOCATION",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "cash-flow",
            "Operating cash flow increased and financial flexibility remained strong.",
            "FINANCIAL_HEALTH",
            "REALIZED",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "export-controls",
            "China export controls may pressure data center revenue in restricted products.",
            "FINANCIAL_HEALTH",
            "RISK_FLAG",
            "DECREASE",
            "MODERATE",
        ),
    ]


def _transcript_specs() -> list[_ClaimSpec]:
    return [
        _ClaimSpec(
            "cloud-demand",
            "Cloud service providers continued to expand demand for AI training capacity.",
            "DEMAND_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "inference-demand",
            "Inference workloads increased and broadened data center demand beyond training.",
            "DEMAND_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "enterprise-ai",
            "Enterprise AI adoption increased as companies deployed copilots and agents.",
            "MARKET_STRUCTURE",
            "GUIDANCE",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "sovereign-ai",
            "Sovereign AI projects increased the pipeline for national AI infrastructure.",
            "MARKET_STRUCTURE",
            "GUIDANCE",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "networking-attach",
            "Networking attach rates increased with larger GPU cluster deployments.",
            "SUPPLY_SIGNAL",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "cuda-moat",
            "CUDA software and libraries strengthened the platform competitive position.",
            "COMPETITIVE_POSITION",
            "STRUCTURAL",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "ecosystem",
            "The developer ecosystem expanded around NVIDIA accelerated computing.",
            "COMPETITIVE_POSITION",
            "STRUCTURAL",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "pricing",
            "Strong product value supported pricing for the newest data center systems.",
            "PRICING_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "supply-growth",
            "Supply availability improved as partners scaled capacity for Blackwell systems.",
            "SUPPLY_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "lead-times",
            "Lead times improved compared with prior constrained periods.",
            "SUPPLY_SIGNAL",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "rd-investment",
            "Research and development expense increased to support future architecture launches.",
            "COST_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "sg-and-a",
            "Sales, general and administrative investment increased with enterprise demand coverage.",
            "COST_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "WEAK",
        ),
        _ClaimSpec(
            "cash-generation",
            "Free cash flow increased as revenue growth converted into operating cash.",
            "FINANCIAL_HEALTH",
            "REALIZED",
            "INCREASE",
            "STRONG",
        ),
        _ClaimSpec(
            "balance-sheet",
            "The balance sheet remained strong with substantial cash and marketable securities.",
            "FINANCIAL_HEALTH",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "customer-concentration",
            "Several large customers represented a concentrated portion of data center revenue.",
            "DEMAND_SIGNAL",
            "RISK_FLAG",
            "DECREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "export-risk",
            "Export control restrictions in China remained a risk to revenue growth.",
            "FINANCIAL_HEALTH",
            "RISK_FLAG",
            "DECREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "execution",
            "Management execution improved as complex platform transitions stayed on schedule.",
            "GOVERNANCE",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "automotive",
            "Automotive design wins increased for future autonomous driving platforms.",
            "DEMAND_SIGNAL",
            "GUIDANCE",
            "INCREASE",
            "WEAK",
        ),
        _ClaimSpec(
            "gaming",
            "Gaming demand improved as new graphics products refreshed the installed base.",
            "DEMAND_SIGNAL",
            "REALIZED",
            "INCREASE",
            "WEAK",
        ),
        _ClaimSpec(
            "professional-visualization",
            "Professional visualization demand increased with workstation AI workloads.",
            "DEMAND_SIGNAL",
            "REALIZED",
            "INCREASE",
            "WEAK",
        ),
        _ClaimSpec(
            "macro-rates",
            "Higher interest rates remained a macro risk for customer capital spending.",
            "MACRO_EXPOSURE",
            "EXTERNAL",
            "DECREASE",
            "WEAK",
        ),
        _ClaimSpec(
            "liquidity",
            "Liquidity remained ample relative to debt and lease obligations.",
            "FINANCIAL_HEALTH",
            "REALIZED",
            "INCREASE",
            "MODERATE",
        ),
        _ClaimSpec(
            "capital-return",
            "Capital return increased while management continued to fund strategic investment.",
            "CAPITAL_ALLOCATION",
            "REALIZED",
            "INCREASE",
            "WEAK",
        ),
    ]


def _claims(
    *,
    prefix: str,
    specs: list[_ClaimSpec],
    chunk_id: str,
    source_ref: SourceRef,
    published_date: date,
) -> list[Claim]:
    return [
        _claim(
            claim_id=f"{prefix}-{spec.suffix}",
            spec=spec,
            chunk_id=chunk_id,
            source_ref=source_ref,
            published_date=published_date,
        )
        for spec in specs
    ]


def _claim(
    *,
    claim_id: str,
    spec: _ClaimSpec,
    chunk_id: str,
    source_ref: SourceRef,
    published_date: date,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=spec.text,
        claim_subject=spec.subject,
        claim_nature=spec.nature,
        direction=spec.direction,
        magnitude_qualifier=spec.magnitude,
        macro_variable="RATE" if spec.subject == "MACRO_EXPOSURE" else None,
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.95,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=source_ref,
        chunk_ref=chunk_id,
        published_date=published_date,
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
    discovery_channel: Literal["direct", "edgar_api"], content_source: Literal["8-K", "earnings_call"]
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
    lower = NVDA_DAMODARAN_REF_USD * (1 - _VALUATION_BAND)
    upper = NVDA_DAMODARAN_REF_USD * (1 + _VALUATION_BAND)
    assert lower <= value <= upper
    return value
