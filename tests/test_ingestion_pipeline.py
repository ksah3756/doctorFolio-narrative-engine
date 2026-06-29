from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

from dcf_engine.ingestion.pipeline import run_ingestion_pipeline

from dcf_engine.claim import SOURCE_RELIABILITY, Claim, ExtractionQuality, SourceRef
from dcf_engine.extraction.client import ExtractionResponse, TokenUsage
from dcf_engine.ingestion import JsonClaimStore, SourceDocument


class FixtureFetcher:
    def __init__(self, documents: list[SourceDocument]) -> None:
        self._documents = documents
        self.calls = 0

    def fetch(self) -> Iterable[SourceDocument]:
        self.calls += 1
        return self._documents


class RecordingExtractor:
    def __init__(self, outcomes: dict[str, ExtractionResponse | Exception]) -> None:
        self._outcomes = outcomes
        self.calls: list[str] = []

    def extract_claims(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        self.calls.append(chunk_id)
        outcome = self._outcomes[chunk_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _source_ref() -> SourceRef:
    return SourceRef(
        discovery_channel="edgar_api",
        content_source="10-Q",
        source_reliability=SOURCE_RELIABILITY["10-Q"],
    )


def _document(*, doc_id: str = "nvda-doc", raw_text: str | None = None) -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        url=f"https://example.test/{doc_id}",
        title="10-Q - NVIDIA CORP",
        published_date=date(2026, 6, 24),
        source_ref=_source_ref(),
        raw_text=raw_text
        or "Data center revenue increased as cloud customers expanded deployment plans.",
    )


def _claim(*, claim_id: str, chunk_id: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="Data center revenue increased as cloud customers expanded deployment plans.",
        claim_subject="DEMAND_SIGNAL",
        claim_nature="REALIZED",
        direction="INCREASE",
        magnitude_qualifier="STRONG",
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.9,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=_source_ref(),
        chunk_ref=chunk_id,
        published_date=date(2026, 6, 24),
    )


def _response(
    *,
    chunk_id: str,
    claims: list[Claim] | None = None,
    schema_valid: bool = True,
    error: str | None = None,
) -> ExtractionResponse:
    return ExtractionResponse(
        chunk_id=chunk_id,
        claims=claims or [],
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        latency_ms=25,
        schema_valid=schema_valid,
        error=error,
    )


def test_pipeline_stores_source_chunks_claims_and_returns_counts(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document()
    chunk_id = "nvda-doc-0001"
    fetcher = FixtureFetcher([document])
    extractor = RecordingExtractor(
        {
            chunk_id: _response(
                chunk_id=chunk_id,
                claims=[_claim(claim_id="claim-a", chunk_id=chunk_id)],
            )
        }
    )

    result = run_ingestion_pipeline(fetchers=[fetcher], store=store, extractor=extractor)

    assert result.documents_fetched == 1
    assert result.documents_processed == 1
    assert result.documents_skipped == 0
    assert result.chunks_processed == 1
    assert result.chunks_rejected == 0
    assert result.claims_saved == 1
    assert result.error_count == 0
    assert store.load_source(document.doc_id) == document
    assert store.load_chunk(doc_id=document.doc_id, chunk_id=chunk_id).text == document.raw_text
    assert store.load_all_claims() == [_claim(claim_id="claim-a", chunk_id=chunk_id)]


def test_processed_doc_id_is_skipped_without_extraction_or_duplicate_claims(
    tmp_path: Path,
) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(doc_id="repeat-doc")
    chunk_id = "repeat-doc-0001"
    first_extractor = RecordingExtractor(
        {
            chunk_id: _response(
                chunk_id=chunk_id,
                claims=[_claim(claim_id="claim-a", chunk_id=chunk_id)],
            )
        }
    )

    first = run_ingestion_pipeline(
        fetchers=[FixtureFetcher([document])], store=store, extractor=first_extractor
    )
    second_extractor = RecordingExtractor({chunk_id: AssertionError("must not extract")})
    second = run_ingestion_pipeline(
        fetchers=[FixtureFetcher([document])], store=store, extractor=second_extractor
    )

    assert first.claims_saved == 1
    assert second.documents_fetched == 1
    assert second.documents_processed == 0
    assert second.documents_skipped == 1
    assert second.chunks_processed == 0
    assert second.claims_saved == 0
    assert second_extractor.calls == []
    assert store.load_all_claims() == [_claim(claim_id="claim-a", chunk_id=chunk_id)]


def test_schema_invalid_response_rejects_chunk_without_saving_claims(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(doc_id="invalid-doc")
    chunk_id = "invalid-doc-0001"
    fetcher = FixtureFetcher([document])
    extractor = RecordingExtractor(
        {
            chunk_id: _response(
                chunk_id=chunk_id,
                claims=[_claim(claim_id="invalid-claim", chunk_id=chunk_id)],
                schema_valid=False,
                error="invalid extraction payload",
            )
        }
    )

    result = run_ingestion_pipeline(fetchers=[fetcher], store=store, extractor=extractor)

    assert result.documents_processed == 1
    assert result.chunks_processed == 1
    assert result.chunks_rejected == 1
    assert result.claims_saved == 0
    assert result.error_count == 1
    assert result.errors[0].chunk_id == chunk_id
    assert store.load_all_claims() == []


def test_extractor_exception_is_captured_and_later_chunks_continue(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(
        doc_id="multi-doc",
        raw_text=(
            "Revenue accelerated as cloud demand expanded. "
            "Supply improved as new capacity came online."
        ),
    )
    first_chunk_id = "multi-doc-0001"
    second_chunk_id = "multi-doc-0002"
    fetcher = FixtureFetcher([document])
    extractor = RecordingExtractor(
        {
            first_chunk_id: RuntimeError("provider timeout"),
            second_chunk_id: _response(
                chunk_id=second_chunk_id,
                claims=[_claim(claim_id="claim-after-error", chunk_id=second_chunk_id)],
            ),
        }
    )

    result = run_ingestion_pipeline(
        fetchers=[fetcher], store=store, extractor=extractor, max_tokens=7
    )

    assert result.documents_processed == 1
    assert result.chunks_processed == 2
    assert result.claims_saved == 1
    assert result.error_count == 1
    assert result.errors[0].chunk_id == first_chunk_id
    assert "provider timeout" in result.errors[0].message
    assert extractor.calls == [first_chunk_id, second_chunk_id]
    assert store.load_all_claims() == [
        _claim(claim_id="claim-after-error", chunk_id=second_chunk_id)
    ]


def test_load_all_claims_returns_pipeline_saved_claim_objects_in_store_order(
    tmp_path: Path,
) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(
        doc_id="ordered-doc",
        raw_text=(
            "Revenue accelerated as cloud demand expanded. "
            "Supply improved as new capacity came online."
        ),
    )
    first_chunk_id = "ordered-doc-0001"
    second_chunk_id = "ordered-doc-0002"
    first_claim = _claim(claim_id="claim-a", chunk_id=first_chunk_id)
    second_claim = _claim(claim_id="claim-b", chunk_id=second_chunk_id)
    extractor = RecordingExtractor(
        {
            first_chunk_id: _response(chunk_id=first_chunk_id, claims=[first_claim]),
            second_chunk_id: _response(chunk_id=second_chunk_id, claims=[second_claim]),
        }
    )

    run_ingestion_pipeline(
        fetchers=[FixtureFetcher([document])], store=store, extractor=extractor, max_tokens=7
    )

    loaded = store.load_all_claims()
    assert loaded == [first_claim, second_claim]
    assert all(isinstance(claim, Claim) for claim in loaded)
