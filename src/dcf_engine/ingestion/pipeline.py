"""Deterministic local ingestion pipeline boundary."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from dcf_engine.extraction.client import ExtractionResponse
from dcf_engine.ingestion.chunker import DEFAULT_MAX_TOKENS, Chunk, chunk_document
from dcf_engine.ingestion.fetcher import SourceDocument
from dcf_engine.ingestion.store import JsonClaimStore


class SourceFetcher(Protocol):
    """Narrow provider adapter contract for local ingestion runs."""

    def fetch(self) -> Iterable[SourceDocument]:
        """Return source documents ready for chunking."""


class ExtractionClient(Protocol):
    """Narrow extraction contract shared by live clients and deterministic tests."""

    def extract_claims(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        """Extract validated claims from one chunk."""


@dataclass(frozen=True)
class IngestionError:
    doc_id: str
    chunk_id: str | None
    message: str


@dataclass(frozen=True)
class IngestionResult:
    documents_fetched: int
    documents_processed: int
    documents_skipped: int
    chunks_processed: int
    chunks_rejected: int
    claims_saved: int
    errors: tuple[IngestionError, ...]

    @property
    def error_count(self) -> int:
        return len(self.errors)


@dataclass
class _IngestionCounters:
    documents_fetched: int = 0
    documents_processed: int = 0
    documents_skipped: int = 0
    chunks_processed: int = 0
    chunks_rejected: int = 0
    claims_saved: int = 0


def run_ingestion_pipeline(
    *,
    fetchers: Sequence[SourceFetcher],
    store: JsonClaimStore,
    extractor: ExtractionClient,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> IngestionResult:
    """Fetch, chunk, extract, and persist source documents into a JsonClaimStore."""

    counters = _IngestionCounters()
    errors: list[IngestionError] = []

    for fetcher in fetchers:
        for document in fetcher.fetch():
            counters.documents_fetched += 1
            if store.is_processed(document.doc_id):
                counters.documents_skipped += 1
                continue

            chunks = chunk_document(document, max_tokens=max_tokens)
            for chunk in chunks:
                counters.chunks_processed += 1
                store.save_chunk(chunk)
                response = _extract_or_error(
                    extractor=extractor,
                    document=document,
                    chunk_id=chunk.chunk_id,
                    chunk_text=chunk.text,
                    errors=errors,
                )
                if response is None:
                    continue
                if not response.schema_valid:
                    counters.chunks_rejected += 1
                    errors.append(
                        IngestionError(
                            doc_id=document.doc_id,
                            chunk_id=chunk.chunk_id,
                            message=response.error or "schema validation failed",
                        )
                    )
                    continue
                if response.chunk_id != chunk.chunk_id:
                    counters.chunks_rejected += 1
                    errors.append(
                        IngestionError(
                            doc_id=document.doc_id,
                            chunk_id=chunk.chunk_id,
                            message=(
                                f"extraction response chunk_id {response.chunk_id!r} "
                                f"did not match {chunk.chunk_id!r}"
                            ),
                        )
                    )
                    continue

                provenance_error = _claim_provenance_error(
                    response=response, document=document, chunk=chunk
                )
                if provenance_error is not None:
                    counters.chunks_rejected += 1
                    errors.append(
                        IngestionError(
                            doc_id=document.doc_id,
                            chunk_id=chunk.chunk_id,
                            message=provenance_error,
                        )
                    )
                    continue

                store.save_claims(chunk.chunk_id, response.claims)
                counters.claims_saved += len(response.claims)

            # save_source가 processed 표시까지 담당하므로 문서 단위 작업 뒤에 호출한다.
            store.save_source(document)
            counters.documents_processed += 1

    return IngestionResult(
        documents_fetched=counters.documents_fetched,
        documents_processed=counters.documents_processed,
        documents_skipped=counters.documents_skipped,
        chunks_processed=counters.chunks_processed,
        chunks_rejected=counters.chunks_rejected,
        claims_saved=counters.claims_saved,
        errors=tuple(errors),
    )


def _extract_or_error(
    *,
    extractor: ExtractionClient,
    document: SourceDocument,
    chunk_id: str,
    chunk_text: str,
    errors: list[IngestionError],
) -> ExtractionResponse | None:
    try:
        return extractor.extract_claims(chunk_id=chunk_id, chunk_text=chunk_text)
    except Exception as exc:
        errors.append(
            IngestionError(
                doc_id=document.doc_id,
                chunk_id=chunk_id,
                message=f"{type(exc).__name__}: {exc}",
            )
        )
        return None


def _claim_provenance_error(
    *, response: ExtractionResponse, document: SourceDocument, chunk: Chunk
) -> str | None:
    for claim in response.claims:
        mismatches: list[str] = []
        if claim.chunk_ref != chunk.chunk_id:
            mismatches.append(
                f"chunk_ref {claim.chunk_ref!r} did not match {chunk.chunk_id!r}"
            )
        if claim.source_ref != chunk.source_ref:
            mismatches.append("source_ref did not match chunk source_ref")
        if claim.published_date != document.published_date:
            mismatches.append(
                f"published_date {claim.published_date.isoformat()!r} "
                f"did not match {document.published_date.isoformat()!r}"
            )
        if mismatches:
            return f"claim provenance mismatch for {claim.claim_id!r}: {'; '.join(mismatches)}"
    return None
