"""Deterministic source document chunking for extraction inputs."""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from dcf_engine.claim import SourceRef
from dcf_engine.ingestion.fetcher import SourceDocument

DEFAULT_MAX_TOKENS: Final[int] = 500
MIN_MERGE_CHARS: Final[int] = 100
_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(r"[^.!?]+[.!?]+|[^.!?]+")
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\S+")


class Chunk(BaseModel):
    """Extraction-ready text chunk with source-document traceability."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    sequence: int = Field(ge=1)
    text: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    source_ref: SourceRef


def chunk_document(
    document: SourceDocument, *, max_tokens: int = DEFAULT_MAX_TOKENS
) -> list[Chunk]:
    """Split one source document into ordered, traceable extraction chunks."""

    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if not document.raw_text.strip():
        raise ValueError("raw_text must contain non-whitespace text")

    spans = _merge_short_spans(
        _budgeted_unit_spans(document.raw_text, max_tokens), document.raw_text, max_tokens
    )
    return [
        Chunk(
            chunk_id=f"{document.doc_id}-{sequence:04d}",
            doc_id=document.doc_id,
            sequence=sequence,
            text=document.raw_text[start:end],
            char_start=start,
            char_end=end,
            source_ref=document.source_ref,
        )
        for sequence, (start, end) in enumerate(spans, start=1)
    ]


def _budgeted_unit_spans(text: str, max_tokens: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for start, end in _paragraph_spans(text):
        unit_text = text[start:end]
        if _token_count(unit_text) <= max_tokens:
            spans.append((start, end))
            continue
        spans.extend(_sentence_or_word_spans(text, start, end, max_tokens))
    return spans


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    paragraph_start: int | None = None
    paragraph_end = 0
    offset = 0

    for line in text.splitlines(keepends=True):
        line_start = offset
        line_end = line_start + len(line)
        if line.strip():
            if paragraph_start is None:
                paragraph_start = line_start
            paragraph_end = line_start + len(line.rstrip("\r\n"))
        elif paragraph_start is not None:
            spans.append((paragraph_start, paragraph_end))
            paragraph_start = None
        offset = line_end

    if paragraph_start is not None:
        spans.append((paragraph_start, paragraph_end))
    return spans


def _sentence_or_word_spans(
    text: str, start: int, end: int, max_tokens: int
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for relative_match in _SENTENCE_RE.finditer(text[start:end]):
        sentence_start = start + relative_match.start()
        sentence_end = start + relative_match.end()
        sentence_start, sentence_end = _trim_span(text, sentence_start, sentence_end)
        if sentence_start == sentence_end:
            continue
        if _token_count(text[sentence_start:sentence_end]) <= max_tokens:
            spans.append((sentence_start, sentence_end))
        else:
            spans.extend(_word_budget_spans(text, sentence_start, sentence_end, max_tokens))
    return spans


def _word_budget_spans(
    text: str, start: int, end: int, max_tokens: int
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    chunk_start: int | None = None
    chunk_end = start
    token_count = 0

    for match in _WORD_RE.finditer(text[start:end]):
        word_start = start + match.start()
        word_end = start + match.end()
        if chunk_start is None:
            chunk_start = word_start
        if token_count == max_tokens:
            spans.append((chunk_start, chunk_end))
            chunk_start = word_start
            token_count = 0
        chunk_end = word_end
        token_count += 1

    if chunk_start is not None:
        spans.append((chunk_start, chunk_end))
    return spans


def _merge_short_spans(
    spans: list[tuple[int, int]], text: str, max_tokens: int
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged:
            merged.append((start, end))
            continue

        previous_start, previous_end = merged[-1]
        candidate = (previous_start, end)
        current_text = text[start:end]
        candidate_text = text[candidate[0] : candidate[1]]
        if len(current_text) < MIN_MERGE_CHARS and _token_count(candidate_text) <= max_tokens:
            merged[-1] = candidate
        else:
            merged.append((start, end))
    return merged


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _token_count(text: str) -> int:
    return len(text.split())
