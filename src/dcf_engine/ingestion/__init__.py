"""Source ingestion boundaries."""

from dcf_engine.ingestion.chunker import Chunk, chunk_document
from dcf_engine.ingestion.fetcher import (
    EdgarRssFetcher,
    ManualTranscriptLoader,
    ReutersRssFetcher,
    SourceDocument,
)
from dcf_engine.ingestion.pipeline import (
    ExtractionClient,
    IngestionError,
    IngestionResult,
    SourceFetcher,
    run_ingestion_pipeline,
)
from dcf_engine.ingestion.store import JsonClaimStore, JsonClaimStoreError

__all__ = [
    "Chunk",
    "EdgarRssFetcher",
    "ExtractionClient",
    "IngestionError",
    "IngestionResult",
    "JsonClaimStore",
    "JsonClaimStoreError",
    "ManualTranscriptLoader",
    "ReutersRssFetcher",
    "SourceDocument",
    "SourceFetcher",
    "chunk_document",
    "run_ingestion_pipeline",
]
