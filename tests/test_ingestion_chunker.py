from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from dcf_engine.claim import SOURCE_RELIABILITY, SourceRef
from dcf_engine.ingestion import Chunk, SourceDocument, chunk_document


def _source_ref() -> SourceRef:
    return SourceRef(
        discovery_channel="edgar_api",
        content_source="10-Q",
        source_reliability=SOURCE_RELIABILITY["10-Q"],
    )


def _document(raw_text: str, *, doc_id: str = "nvda-10q") -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        url=f"https://example.test/{doc_id}",
        title="10-Q - NVIDIA CORP",
        published_date=date(2026, 6, 24),
        source_ref=_source_ref(),
        raw_text=raw_text,
    )


def test_single_document_chunks_are_ordered_traceable_and_immutable() -> None:
    document = _document(
        "Data center revenue grew sharply on demand for accelerated computing.\n\n"
        "Management expects supply to improve through the second half of the fiscal year."
    )

    chunks = chunk_document(document, max_tokens=80)

    assert [chunk.chunk_id for chunk in chunks] == ["nvda-10q-0001"]
    assert [chunk.sequence for chunk in chunks] == [1]
    assert chunks[0].doc_id == document.doc_id
    assert chunks[0].source_ref == document.source_ref
    assert chunks[0].text == document.raw_text
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(document.raw_text)
    with pytest.raises(ValidationError):
        chunks[0].text = "mutated"


def test_long_text_splits_on_sentence_boundaries_under_small_token_budget() -> None:
    document = _document(
        "Revenue accelerated as cloud customers expanded deployment plans. "
        "Supply constraints eased as new capacity came online. "
        "Management expects additional platform transitions next quarter.",
        doc_id="long-doc",
    )

    chunks = chunk_document(document, max_tokens=8)

    assert [chunk.chunk_id for chunk in chunks] == [
        "long-doc-0001",
        "long-doc-0002",
        "long-doc-0003",
    ]
    assert [chunk.text for chunk in chunks] == [
        "Revenue accelerated as cloud customers expanded deployment plans.",
        "Supply constraints eased as new capacity came online.",
        "Management expects additional platform transitions next quarter.",
    ]
    assert all(len(chunk.text.split()) <= 8 for chunk in chunks)


def test_short_paragraphs_merge_into_previous_chunk_when_possible() -> None:
    first = "This paragraph is long enough to become the initial chunk without help."
    short = "Short note."
    document = _document(f"{first}\n\n{short}", doc_id="merge-doc")

    chunks = chunk_document(document, max_tokens=80)

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "merge-doc-0001"
    assert chunks[0].text == f"{first}\n\n{short}"


def test_chunk_offsets_map_back_to_original_raw_text() -> None:
    document = _document(
        "First paragraph has enough substance to stand on its own.\n\n"
        "Second paragraph has enough substance to form another chunk.\n\n"
        "Third paragraph has enough substance to complete the sample.",
        doc_id="offset-doc",
    )

    chunks = chunk_document(document, max_tokens=10)

    assert len(chunks) == 3
    for chunk in chunks:
        assert document.raw_text[chunk.char_start : chunk.char_end] == chunk.text


@pytest.mark.parametrize("raw_text", ["", "   \n\t  "])
def test_empty_or_whitespace_only_documents_are_rejected(raw_text: str) -> None:
    with pytest.raises(ValueError, match="raw_text must contain non-whitespace text"):
        chunk_document(_document(raw_text), max_tokens=80)
