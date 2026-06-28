"""Local JSON persistence for ingestion artifacts."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Final, cast

from pydantic import ValidationError

from dcf_engine.claim import Claim
from dcf_engine.ingestion.chunker import Chunk
from dcf_engine.ingestion.fetcher import SourceDocument

_DEFAULT_TICKER: Final[str] = "NVDA"
_STATE_FILE: Final[str] = "pipeline_state.json"
_INVALID_ARTIFACT_IDS: Final[frozenset[str]] = frozenset({"", ".", ".."})


class JsonClaimStoreError(RuntimeError):
    """Raised when persisted ingestion JSON cannot be loaded safely."""


class JsonClaimStore:
    """Deterministic local JSON store for M2 ingestion artifacts."""

    def __init__(self, data_dir: Path, *, ticker: str = _DEFAULT_TICKER) -> None:
        self._data_dir = data_dir
        self._ticker = ticker

    def is_processed(self, doc_id: str) -> bool:
        return doc_id in self._processed_doc_ids(self._ticker)

    def save_source(self, document: SourceDocument) -> None:
        ticker_dir = self._ticker_dir(self._ticker)
        self._write_json(
            _artifact_path(ticker_dir / "sources", document.doc_id),
            document.model_dump(mode="json"),
        )
        self._mark_processed(document.doc_id)

    def load_source(self, doc_id: str) -> SourceDocument:
        path = _artifact_path(self._ticker_dir(self._ticker) / "sources", doc_id)
        try:
            return SourceDocument.model_validate(_read_json(path))
        except ValidationError as error:
            raise JsonClaimStoreError(f"Invalid source document JSON in {path.name}") from error

    def save_chunk(self, chunk: Chunk) -> None:
        self._write_json(
            _artifact_path(self._ticker_dir(self._ticker) / "chunks", chunk.chunk_id),
            chunk.model_dump(mode="json"),
        )

    def load_chunk(self, *, doc_id: str, chunk_id: str) -> Chunk:
        path = _artifact_path(self._ticker_dir(self._ticker) / "chunks", chunk_id)
        try:
            chunk = Chunk.model_validate(_read_json(path))
        except ValidationError as error:
            raise JsonClaimStoreError(f"Invalid chunk JSON in {path.name}") from error
        if chunk.doc_id != doc_id:
            raise JsonClaimStoreError(f"Chunk {chunk_id} does not belong to document {doc_id}")
        return chunk

    def save_claims(self, chunk_id: str, claims: list[Claim]) -> None:
        self._write_json(
            _artifact_path(self._ticker_dir(self._ticker) / "claims", chunk_id),
            [claim.model_dump(mode="json") for claim in claims],
        )

    def load_all_claims(self, *, ticker: str | None = None) -> list[Claim]:
        claims_dir = self._ticker_dir(ticker or self._ticker) / "claims"
        if not claims_dir.exists():
            return []

        claims: list[Claim] = []
        for path in sorted(claims_dir.glob("*.json")):
            data = _read_json(path)
            if not isinstance(data, list):
                raise JsonClaimStoreError(f"Claim file {path.name} must contain a JSON list")
            try:
                claims.extend(Claim.model_validate(item) for item in data)
            except ValidationError as error:
                raise JsonClaimStoreError(f"Invalid claim JSON in {path.name}") from error
        return claims

    def _mark_processed(self, doc_id: str) -> None:
        processed_doc_ids = self._processed_doc_ids(self._ticker)
        processed_doc_ids.add(doc_id)
        self._write_json(
            self._state_path(self._ticker),
            {"processed_doc_ids": sorted(processed_doc_ids)},
        )

    def _processed_doc_ids(self, ticker: str) -> set[str]:
        path = self._state_path(ticker)
        if not path.exists():
            return set()

        data = _read_json(path)
        if not isinstance(data, dict):
            raise JsonClaimStoreError(f"Pipeline state {path.name} must contain a JSON object")
        processed = data.get("processed_doc_ids")
        if not isinstance(processed, list) or not all(
            isinstance(doc_id, str) for doc_id in processed
        ):
            raise JsonClaimStoreError(
                f"Pipeline state {path.name} must contain processed_doc_ids strings"
            )
        return set(processed)

    def _state_path(self, ticker: str) -> Path:
        return self._ticker_dir(ticker) / _STATE_FILE

    def _ticker_dir(self, ticker: str) -> Path:
        return self._data_dir / ticker.lower()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _artifact_path(directory: Path, artifact_id: str) -> Path:
    if (
        artifact_id in _INVALID_ARTIFACT_IDS
        or Path(artifact_id).is_absolute()
        or "/" in artifact_id
        or "\\" in artifact_id
    ):
        raise JsonClaimStoreError(f"Invalid artifact id: {artifact_id!r}")
    return directory / f"{artifact_id}.json"


def _read_json(path: Path) -> object:
    try:
        text = path.read_text()
    except OSError as error:
        raise JsonClaimStoreError(f"Could not read JSON store file {path.name}") from error
    if not text.strip():
        raise JsonClaimStoreError(f"JSON store file {path.name} is empty")
    try:
        return cast(object, json.loads(text))
    except JSONDecodeError as error:
        raise JsonClaimStoreError(f"JSON store file {path.name} is corrupt") from error
