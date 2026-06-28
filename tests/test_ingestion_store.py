from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dcf_engine.claim import Claim, ExtractionQuality, SOURCE_RELIABILITY, SourceRef
from dcf_engine.ingestion import Chunk, JsonClaimStore, JsonClaimStoreError, SourceDocument


def _source_ref() -> SourceRef:
    return SourceRef(
        discovery_channel="edgar_api",
        content_source="10-Q",
        source_reliability=SOURCE_RELIABILITY["10-Q"],
    )


def _document(*, doc_id: str = "nvda-doc") -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        url=f"https://example.test/{doc_id}",
        title="10-Q - NVIDIA CORP",
        published_date=date(2026, 6, 24),
        source_ref=_source_ref(),
        raw_text="Data center revenue increased as cloud demand expanded.",
    )


def _chunk(*, doc_id: str = "nvda-doc", chunk_id: str = "nvda-doc-0001") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        sequence=1,
        text="Data center revenue increased as cloud demand expanded.",
        char_start=0,
        char_end=56,
        source_ref=_source_ref(),
    )


def _claim(*, claim_id: str, chunk_id: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="Data center revenue increased as cloud demand expanded.",
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


def test_source_document_round_trips_with_source_ref_metadata(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document()

    store.save_source(document)

    assert store.load_source(document.doc_id) == document
    payload = json.loads((tmp_path / "nvda/sources/nvda-doc.json").read_text())
    assert payload["source_ref"] == {
        "discovery_channel": "edgar_api",
        "content_source": "10-Q",
        "source_reliability": SOURCE_RELIABILITY["10-Q"],
    }


def test_chunk_round_trips_by_doc_id_and_chunk_id_with_offsets(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    chunk = _chunk()

    store.save_chunk(chunk)

    assert store.load_chunk(doc_id=chunk.doc_id, chunk_id=chunk.chunk_id) == chunk
    payload = json.loads((tmp_path / "nvda/chunks/nvda-doc-0001.json").read_text())
    assert payload["doc_id"] == "nvda-doc"
    assert payload["chunk_id"] == "nvda-doc-0001"
    assert payload["char_start"] == 0
    assert payload["char_end"] == 56
    assert payload["source_ref"]["content_source"] == "10-Q"


def test_save_claims_loads_typed_claims_in_deterministic_order(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    later_chunk_claim = _claim(claim_id="claim-c", chunk_id="nvda-doc-0002")
    first_claim = _claim(claim_id="claim-a", chunk_id="nvda-doc-0001")
    second_claim = _claim(claim_id="claim-b", chunk_id="nvda-doc-0001")

    store.save_claims("nvda-doc-0002", [later_chunk_claim])
    store.save_claims("nvda-doc-0001", [first_claim, second_claim])

    loaded = store.load_all_claims(ticker="NVDA")

    assert loaded == [first_claim, second_claim, later_chunk_claim]
    assert all(isinstance(claim, Claim) for claim in loaded)


def test_processed_state_is_idempotent_across_repeated_source_saves(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    document = _document(doc_id="repeat-doc")

    assert not store.is_processed("repeat-doc")
    store.save_source(document)
    store.save_source(document)

    assert store.is_processed("repeat-doc")
    state = json.loads((tmp_path / "nvda/pipeline_state.json").read_text())
    assert state == {"processed_doc_ids": ["repeat-doc"]}


@pytest.mark.parametrize("payload", ["", "{not-json"])
def test_empty_or_corrupt_claim_files_fail_clearly(tmp_path: Path, payload: str) -> None:
    claims_dir = tmp_path / "nvda/claims"
    claims_dir.mkdir(parents=True)
    (claims_dir / "nvda-doc-0001.json").write_text(payload)

    store = JsonClaimStore(tmp_path)

    with pytest.raises(JsonClaimStoreError, match="nvda-doc-0001.json"):
        store.load_all_claims(ticker="NVDA")
