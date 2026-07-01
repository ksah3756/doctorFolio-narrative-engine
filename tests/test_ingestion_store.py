from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dcf_engine.claim import SOURCE_RELIABILITY, Claim, ExtractionQuality, SourceRef
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
        char_end=55,
        source_ref=_source_ref(),
    )


def _claim(*, claim_id: str, chunk_id: str, verbatim_overlap: float = 0.9) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="Data center revenue increased as cloud demand expanded.",
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
    assert payload["char_end"] == 55
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


def test_low_overlap_claims_are_saved_to_quarantine_artifact(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    low_overlap_claim = _claim(
        claim_id="claim-low-overlap",
        chunk_id="nvda-doc-0001",
        verbatim_overlap=0.79,
    )

    store.save_claims("nvda-doc-0001", [low_overlap_claim])

    assert not (tmp_path / "nvda/claims/nvda-doc-0001.json").exists()
    quarantine_path = tmp_path / "nvda/quarantined_claims/nvda-doc-0001.json"
    assert quarantine_path.exists()
    payload = json.loads(quarantine_path.read_text())
    assert payload[0]["claim_id"] == "claim-low-overlap"
    assert store.load_all_claims() == []
    assert store.load_all_claims(include_quarantined=True) == [low_overlap_claim]


def test_boundary_overlap_claim_is_trusted_and_loaded_by_default(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    boundary_claim = _claim(
        claim_id="claim-boundary-overlap",
        chunk_id="nvda-doc-0001",
        verbatim_overlap=0.8,
    )

    store.save_claims("nvda-doc-0001", [boundary_claim])

    claims_path = tmp_path / "nvda/claims/nvda-doc-0001.json"
    assert claims_path.exists()
    assert not (tmp_path / "nvda/quarantined_claims/nvda-doc-0001.json").exists()
    assert store.load_all_claims() == [boundary_claim]


def test_load_all_claims_can_include_quarantined_claims_for_audit(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path)
    trusted_claim = _claim(claim_id="claim-trusted", chunk_id="nvda-doc-0001")
    quarantined_claim = _claim(
        claim_id="claim-quarantined",
        chunk_id="nvda-doc-0001",
        verbatim_overlap=0.1,
    )

    store.save_claims("nvda-doc-0001", [trusted_claim, quarantined_claim])

    assert store.load_all_claims() == [trusted_claim]
    assert store.load_all_claims(include_quarantined=True) == [
        trusted_claim,
        quarantined_claim,
    ]


def test_load_all_claims_defaults_to_instance_ticker(tmp_path: Path) -> None:
    store = JsonClaimStore(tmp_path, ticker="AAPL")
    claim = _claim(claim_id="claim-a", chunk_id="aapl-doc-0001")

    store.save_claims("aapl-doc-0001", [claim])

    assert store.load_all_claims() == [claim]


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


@pytest.mark.parametrize("doc_id", ["../escape", "/tmp/escape", "nested/doc", "nested\\doc", "."])
def test_source_document_ids_cannot_escape_store_boundary(tmp_path: Path, doc_id: str) -> None:
    store = JsonClaimStore(tmp_path)

    with pytest.raises(JsonClaimStoreError, match="Invalid artifact id"):
        store.save_source(_document(doc_id=doc_id))

    with pytest.raises(JsonClaimStoreError, match="Invalid artifact id"):
        store.load_source(doc_id)
    assert not (tmp_path.parent / "escape.json").exists()


@pytest.mark.parametrize(
    "chunk_id",
    ["../escape", "/tmp/escape", "nested/chunk", "nested\\chunk", "."],
)
def test_chunk_ids_cannot_escape_store_boundary(tmp_path: Path, chunk_id: str) -> None:
    store = JsonClaimStore(tmp_path)

    with pytest.raises(JsonClaimStoreError, match="Invalid artifact id"):
        store.save_chunk(_chunk(chunk_id=chunk_id))

    with pytest.raises(JsonClaimStoreError, match="Invalid artifact id"):
        store.load_chunk(doc_id="nvda-doc", chunk_id=chunk_id)
    assert not (tmp_path.parent / "escape.json").exists()


@pytest.mark.parametrize(
    "chunk_id",
    ["../escape", "/tmp/escape", "nested/chunk", "nested\\chunk", "."],
)
def test_claim_chunk_ids_cannot_escape_store_boundary(tmp_path: Path, chunk_id: str) -> None:
    store = JsonClaimStore(tmp_path)
    claim = _claim(claim_id="claim-a", chunk_id=chunk_id)

    with pytest.raises(JsonClaimStoreError, match="Invalid artifact id"):
        store.save_claims(chunk_id, [claim])
    assert not (tmp_path.parent / "escape.json").exists()
