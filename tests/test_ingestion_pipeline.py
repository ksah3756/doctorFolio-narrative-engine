from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

import pytest

from dcf_engine.claim import SOURCE_RELIABILITY, Claim, ExtractionQuality, SourceRef
from dcf_engine.extraction.client import ExtractionResponse, TokenUsage
from dcf_engine.ingestion import (
    JsonClaimStore,
    ManualTranscriptLoader,
    ReutersRssFetcher,
    SourceDocument,
)
from dcf_engine.ingestion.pipeline import run_ingestion_pipeline


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


def _alternate_source_ref() -> SourceRef:
    return SourceRef(
        discovery_channel="direct",
        content_source="press_release",
        source_reliability=SOURCE_RELIABILITY["press_release"],
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


def _rss_feed(items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Reuters Business News</title>
    {items}
  </channel>
</rss>
"""


def _rss_item(
    *,
    title: str,
    link: str,
    pub_date: str = "Tue, 24 Jun 2026 18:01:05 GMT",
    description: str = "NVIDIA shares rose after analysts cited Blackwell demand.",
) -> str:
    return f"""
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <pubDate>{pub_date}</pubDate>
      <description>{description}</description>
    </item>
"""


def _rss_reader(feed_text: str) -> Callable[[str], str]:
    def read(url: str) -> str:
        assert "reuters" in url.lower()
        return feed_text

    return read


def _claim(*, claim_id: str, chunk_id: str, verbatim_overlap: float = 0.9) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="Data center revenue increased as cloud customers expanded deployment plans.",
        claim_subject="DEMAND_SIGNAL",
        claim_nature="REALIZED",
        direction="INCREASE",
        magnitude_qualifier="STRONG",
        extraction_quality=ExtractionQuality(
            verbatim_overlap=verbatim_overlap,
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
    assert store.load_all_claims(include_quarantined=True) == []
    assert not (tmp_path / "nvda/claims/invalid-doc-0001.json").exists()
    assert not (tmp_path / "nvda/quarantined_claims/invalid-doc-0001.json").exists()


@pytest.mark.parametrize("mismatch", ["chunk_ref", "source_ref", "published_date"])
def test_claim_provenance_mismatch_rejects_chunk_without_saving_claims(
    tmp_path: Path, mismatch: str
) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(doc_id="provenance-doc")
    chunk_id = "provenance-doc-0001"
    claim = _claim(claim_id=f"bad-{mismatch}", chunk_id=chunk_id)
    if mismatch == "chunk_ref":
        claim = claim.model_copy(update={"chunk_ref": "other-doc-0001"})
    elif mismatch == "source_ref":
        claim = claim.model_copy(update={"source_ref": _alternate_source_ref()})
    else:
        claim = claim.model_copy(update={"published_date": date(2026, 6, 23)})
    extractor = RecordingExtractor(
        {chunk_id: _response(chunk_id=chunk_id, claims=[claim])}
    )

    result = run_ingestion_pipeline(
        fetchers=[FixtureFetcher([document])], store=store, extractor=extractor
    )

    assert result.documents_processed == 1
    assert result.chunks_processed == 1
    assert result.chunks_rejected == 1
    assert result.claims_saved == 0
    assert result.error_count == 1
    assert result.errors[0].chunk_id == chunk_id
    assert "claim provenance" in result.errors[0].message
    assert store.load_all_claims() == []


def test_pipeline_quarantines_low_overlap_claims_without_trusting_them(
    tmp_path: Path,
) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(doc_id="low-overlap-doc")
    chunk_id = "low-overlap-doc-0001"
    low_overlap_claim = _claim(
        claim_id="claim-low-overlap",
        chunk_id=chunk_id,
        verbatim_overlap=0.79,
    )
    extractor = RecordingExtractor(
        {chunk_id: _response(chunk_id=chunk_id, claims=[low_overlap_claim])}
    )

    result = run_ingestion_pipeline(
        fetchers=[FixtureFetcher([document])], store=store, extractor=extractor
    )

    assert result.documents_processed == 1
    assert result.chunks_processed == 1
    assert result.chunks_rejected == 0
    assert result.claims_saved == 0
    assert result.claims_quarantined == 1
    assert result.error_count == 0
    assert store.load_all_claims() == []
    assert store.load_all_claims(include_quarantined=True) == [low_overlap_claim]
    assert not (tmp_path / "nvda/claims/low-overlap-doc-0001.json").exists()
    assert (tmp_path / "nvda/quarantined_claims/low-overlap-doc-0001.json").exists()


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


def test_manual_transcript_loader_runs_through_source_fetcher_contract(
    tmp_path: Path,
) -> None:
    transcript_path = tmp_path / "nvda-call.txt"
    transcript_path.write_text("NVIDIA demand increased as Blackwell production ramped.")
    loader = ManualTranscriptLoader(
        path=transcript_path,
        title="NVIDIA earnings call",
        published_date=date(2026, 5, 27),
        source_url="file://nvda-call",
    )
    doc_id = "8ae82c4d1bef"
    chunk_id = f"{doc_id}-0001"
    source_ref = SourceRef(
        discovery_channel="direct",
        content_source="earnings_call",
        source_reliability=SOURCE_RELIABILITY["earnings_call"],
    )
    claim = _claim(claim_id="manual-transcript-claim", chunk_id=chunk_id).model_copy(
        update={
            "source_ref": source_ref,
            "published_date": date(2026, 5, 27),
            "claim_text": "NVIDIA demand increased as Blackwell production ramped.",
        }
    )
    extractor = RecordingExtractor({chunk_id: _response(chunk_id=chunk_id, claims=[claim])})
    store = JsonClaimStore(tmp_path / "store")

    result = run_ingestion_pipeline(fetchers=[loader], store=store, extractor=extractor)

    assert result.documents_fetched == 1
    assert result.documents_processed == 1
    assert result.claims_saved == 1
    assert result.error_count == 0
    assert store.load_source(doc_id).content_source == "earnings_call"
    assert store.load_all_claims() == [claim]


def test_reuters_rss_fetcher_runs_through_source_fetcher_pipeline_contract(
    tmp_path: Path,
) -> None:
    article_url = "https://www.reuters.com/technology/nvidia-blackwell-demand-2026-06-24/"
    feed = _rss_feed(
        _rss_item(
            title="NVIDIA shares rise on Blackwell demand",
            link=article_url,
            description="<p>NVIDIA shares rose after analysts cited Blackwell demand.</p>",
        )
    )
    doc_id = hashlib.sha256(article_url.encode()).hexdigest()[:12]
    chunk_id = f"{doc_id}-0001"
    source_ref = SourceRef(
        discovery_channel="rss_aggregator",
        content_source="reuters",
        source_reliability=SOURCE_RELIABILITY["reuters"],
    )
    claim = _claim(claim_id="reuters-rss-claim", chunk_id=chunk_id).model_copy(
        update={
            "source_ref": source_ref,
            "published_date": date(2026, 6, 24),
            "claim_text": "NVIDIA shares rose after analysts cited Blackwell demand.",
        }
    )
    extractor = RecordingExtractor({chunk_id: _response(chunk_id=chunk_id, claims=[claim])})
    store = JsonClaimStore(tmp_path / "store")

    result = run_ingestion_pipeline(
        fetchers=[ReutersRssFetcher(reader=_rss_reader(feed))],
        store=store,
        extractor=extractor,
    )

    assert result.documents_fetched == 1
    assert result.documents_processed == 1
    assert result.chunks_processed == 1
    assert result.claims_saved == 1
    assert result.error_count == 0
    assert extractor.calls == [chunk_id]
    assert store.load_source(doc_id).source_ref == source_ref
    assert store.load_chunk(doc_id=doc_id, chunk_id=chunk_id).source_ref == source_ref
    assert store.load_all_claims() == [claim]
