"""M2.1 extraction benchmark runner."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from dcf_engine.claim import Claim
from dcf_engine.extraction.client import (
    CLAUDE_HAIKU_MODEL,
    DEEPSEEK_MODEL,
    AnthropicExtractionClient,
    DeepSeekExtractionClient,
    ExtractionResponse,
    TokenUsage,
)
from dcf_engine.extraction.evaluator import (
    EvaluationMetrics,
    evaluate_extraction,
    load_gold_labels,
    numeric_consistency_rate,
    read_json_object,
)
from dcf_engine.extraction.prompt import EXTRACTION_PROMPT_VERSION

type ProviderName = Literal["deepseek", "anthropic"]


@dataclass(frozen=True)
class Pricing:
    input_per_1m_tokens_usd: float
    output_per_1m_tokens_usd: float


MODEL_PRICING: Final[dict[str, Pricing]] = {
    DEEPSEEK_MODEL: Pricing(input_per_1m_tokens_usd=0.14, output_per_1m_tokens_usd=0.28),
    CLAUDE_HAIKU_MODEL: Pricing(input_per_1m_tokens_usd=1.0, output_per_1m_tokens_usd=5.0),
}
RESULTS_DIR: Final = Path("data/benchmark/results")


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    prompt_version: str
    chunk_count: int
    schema_validation_rate: float = Field(ge=0.0, le=1.0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    numeric_consistency_rate: float = Field(ge=0.0, le=1.0)
    total_cost_usd: float
    cost_per_chunk_usd: float
    latency_ms_p50: float
    result_path: str | None = None


class ReplayUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int
    completion_tokens: int


class ReplayResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    latency_ms: int
    usage: ReplayUsage
    claims: list[Claim]


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_cache_miss_per_1m_tokens_usd: float
    output_per_1m_tokens_usd: float


class ReplayFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    captured_at: str
    model: str
    pricing_snapshot: PricingSnapshot
    responses: list[ReplayResponse]


def run_benchmark(
    *,
    chunks_dir: Path,
    gold_path: Path,
    replay_path: Path | None = None,
    provider: ProviderName = "deepseek",
    model: str | None = None,
    save_results: bool = False,
    results_dir: Path | None = None,
) -> BenchmarkResult:
    selected_model = model or _default_model(provider)
    chunks = _load_chunks(chunks_dir)
    gold = load_gold_labels(gold_path)
    responses = (
        _load_replay(replay_path)
        if replay_path is not None
        else _run_live(chunks, provider=provider, model=selected_model)
    )
    expected = _flatten(gold.claims_by_chunk.values())
    actual = _flatten(response.claims for response in responses)
    metrics = evaluate_extraction(expected=expected, actual=actual)
    schema_validation_rate = _schema_validation_rate(responses, chunks)
    pricing = _pricing_for_model(selected_model)
    total_cost = sum(_response_cost(response, pricing=pricing) for response in responses)
    output_path = _write_result(
        result_dir=results_dir or chunks_dir.parents[0] / "results",
        result=_result_payload(
            provider=provider,
            model=selected_model,
            responses=responses,
            metrics=metrics,
            chunk_count=len(chunks),
            schema_validation_rate=schema_validation_rate,
            total_cost=total_cost,
            actual=actual,
        ),
    ) if save_results else None
    return BenchmarkResult(
        model=selected_model,
        prompt_version=EXTRACTION_PROMPT_VERSION,
        chunk_count=len(chunks),
        schema_validation_rate=schema_validation_rate,
        precision=metrics.precision,
        recall=metrics.recall,
        numeric_consistency_rate=numeric_consistency_rate(actual),
        total_cost_usd=total_cost,
        cost_per_chunk_usd=total_cost / len(chunks),
        latency_ms_p50=float(median(response.latency_ms for response in responses)),
        result_path=str(output_path) if output_path is not None else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.1 extraction benchmark")
    parser.add_argument("--provider", choices=["deepseek", "anthropic"], default="deepseek")
    parser.add_argument("--model", default=None)
    parser.add_argument("--replay", type=Path, default=None)
    args = parser.parse_args()
    root = Path.cwd()
    result = run_benchmark(
        chunks_dir=root / "data" / "benchmark" / "chunks",
        gold_path=root / "data" / "benchmark" / "gold.json",
        replay_path=args.replay,
        provider=args.provider,
        model=args.model,
        save_results=True,
        results_dir=root / RESULTS_DIR,
    )
    print(result.model_dump_json(indent=2))


def _load_chunks(chunks_dir: Path) -> dict[str, str]:
    chunks = {path.stem: path.read_text() for path in sorted(chunks_dir.glob("*.txt"))}
    if not chunks:
        raise ValueError(f"No benchmark chunks found in {chunks_dir}")
    return chunks


def _load_replay(replay_path: Path) -> list[ExtractionResponse]:
    replay = ReplayFile.model_validate(read_json_object(replay_path))
    return [
        ExtractionResponse(
            chunk_id=response.chunk_id,
            claims=response.claims,
            usage=TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            ),
            latency_ms=response.latency_ms,
        )
        for response in replay.responses
    ]


def _run_live(
    chunks: Mapping[str, str], *, provider: ProviderName, model: str
) -> list[ExtractionResponse]:
    client = _client_for_provider(provider=provider, model=model)
    return [
        client.extract_claims(chunk_id=chunk_id, chunk_text=chunk_text)
        for chunk_id, chunk_text in chunks.items()
    ]


def _schema_validation_rate(
    responses: Iterable[ExtractionResponse], chunks: Mapping[str, str]
) -> float:
    response_list = list(responses)
    if len(response_list) != len(chunks):
        return 0.0
    if {response.chunk_id for response in response_list} != set(chunks):
        return 0.0
    claim_count = sum(len(response.claims) for response in response_list)
    return 1.0 if claim_count > 0 else 0.0


def _response_cost(response: ExtractionResponse, *, pricing: Pricing) -> float:
    input_cost = response.usage.prompt_tokens * pricing.input_per_1m_tokens_usd / 1_000_000
    output_cost = (
        response.usage.completion_tokens * pricing.output_per_1m_tokens_usd / 1_000_000
    )
    return input_cost + output_cost


def _result_payload(
    *,
    provider: ProviderName,
    model: str,
    responses: list[ExtractionResponse],
    metrics: EvaluationMetrics,
    chunk_count: int,
    schema_validation_rate: float,
    total_cost: float,
    actual: list[Claim],
) -> dict[str, object]:
    return {
        "provider": provider,
        "model": model,
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "chunk_count": chunk_count,
        "schema_validation_rate": schema_validation_rate,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "numeric_consistency_rate": numeric_consistency_rate(actual),
        "total_cost_usd": total_cost,
        "cost_per_chunk_usd": total_cost / chunk_count,
        "latency_ms_p50": float(median(response.latency_ms for response in responses)),
        "responses": [
            {
                "chunk_id": response.chunk_id,
                "latency_ms": response.latency_ms,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
                "claims": [claim.model_dump(mode="json") for claim in response.claims],
            }
            for response in responses
        ],
    }


def _write_result(*, result_dir: Path, result: Mapping[str, object]) -> Path:
    result_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model = result.get("model")
    if not isinstance(model, str):
        raise ValueError("result model must be a string")
    path = result_dir / f"{_filename_model(model)}__{timestamp}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return path


def _flatten(claim_groups: Iterable[Iterable[Claim]]) -> list[Claim]:
    return [claim for claims in claim_groups for claim in claims]


def _default_model(provider: ProviderName) -> str:
    if provider == "deepseek":
        return DEEPSEEK_MODEL
    return CLAUDE_HAIKU_MODEL


def _client_for_provider(
    *, provider: ProviderName, model: str
) -> DeepSeekExtractionClient | AnthropicExtractionClient:
    if provider == "deepseek":
        return DeepSeekExtractionClient(model=model)
    return AnthropicExtractionClient(model=model)


def _pricing_for_model(model: str) -> Pricing:
    try:
        return MODEL_PRICING[model]
    except KeyError as exc:
        raise ValueError(f"No pricing configured for model {model}") from exc


def _filename_model(model: str) -> str:
    return model.replace(".", "-").replace("/", "-")


if __name__ == "__main__":
    main()
