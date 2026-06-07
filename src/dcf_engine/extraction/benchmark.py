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
    Scorecard,
    read_json_object,
    score_extraction,
)
from dcf_engine.extraction.gold import load_gold_facts
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
DEFAULT_GOLD_PATH: Final = Path("data/benchmark/gold_facts.json")


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    prompt_version: str
    chunk_count: int
    schema_validation_rate: float = Field(ge=0.0, le=1.0)
    grounded_precision: float = Field(ge=0.0, le=1.0)
    coverage_recall: float = Field(ge=0.0, le=1.0)
    primary_coverage_recall: float = Field(ge=0.0, le=1.0)
    numeric_grounding_rate: float = Field(ge=0.0, le=1.0)
    direction_accuracy: float = Field(ge=0.0, le=1.0)
    magnitude_accuracy: float = Field(ge=0.0, le=1.0)
    subject_accuracy: float = Field(ge=0.0, le=1.0)
    redundancy_rate: float = Field(ge=0.0, le=1.0)
    true_positives: int
    false_negatives: int
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
    gold_path: Path = DEFAULT_GOLD_PATH,
    replay_path: Path | None = None,
    provider: ProviderName = "deepseek",
    model: str | None = None,
    save_results: bool = False,
    results_dir: Path | None = None,
) -> BenchmarkResult:
    selected_model = model or _default_model(provider)
    chunks = _load_chunks(chunks_dir)
    gold = load_gold_facts(gold_path)
    # 기본 CI는 replay를 사용해 외부 API 비용 없이 평가 로직만 검증한다.
    responses = (
        _load_replay(replay_path)
        if replay_path is not None
        else _run_live(chunks, provider=provider, model=selected_model)
    )
    scorecard = score_extraction(gold=gold, responses=responses, chunk_texts=chunks)
    schema_validation_rate = _schema_validation_rate(responses, chunks)
    # 모델별 단가를 명시적으로 분리해 비용 비교가 provider 변경에 섞이지 않게 한다.
    pricing = _pricing_for_model(selected_model)
    total_cost = sum(_response_cost(response, pricing=pricing) for response in responses)
    cost_per_chunk = _cost_per_chunk(responses, pricing=pricing, chunk_count=len(chunks))
    latency_ms_p50 = _latency_ms_p50(responses)
    output_path = _write_result(
        result_dir=results_dir or chunks_dir.parents[0] / "results",
        result=_result_payload(
            provider=provider,
            model=selected_model,
            responses=responses,
            scorecard=scorecard,
            chunk_count=len(chunks),
            schema_validation_rate=schema_validation_rate,
            total_cost=total_cost,
            cost_per_chunk=cost_per_chunk,
            latency_ms_p50=latency_ms_p50,
        ),
    ) if save_results else None
    return BenchmarkResult(
        model=selected_model,
        prompt_version=EXTRACTION_PROMPT_VERSION,
        chunk_count=len(chunks),
        schema_validation_rate=schema_validation_rate,
        grounded_precision=scorecard.grounded_precision,
        coverage_recall=scorecard.coverage_recall,
        primary_coverage_recall=scorecard.primary_coverage_recall,
        numeric_grounding_rate=scorecard.numeric_grounding_rate,
        direction_accuracy=scorecard.direction_accuracy,
        magnitude_accuracy=scorecard.magnitude_accuracy,
        subject_accuracy=scorecard.subject_accuracy,
        redundancy_rate=scorecard.redundancy_rate,
        true_positives=scorecard.true_positives,
        false_negatives=scorecard.false_negatives,
        total_cost_usd=total_cost,
        cost_per_chunk_usd=cost_per_chunk,
        latency_ms_p50=latency_ms_p50,
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
        gold_path=root / DEFAULT_GOLD_PATH,
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
    # live 실행만 provider client를 만들고, replay 실행은 네트워크 경계를 지나지 않는다.
    client = _client_for_provider(provider=provider, model=model)
    responses: list[ExtractionResponse] = []
    for chunk_id, chunk_text in chunks.items():
        try:
            responses.append(client.extract_claims(chunk_id=chunk_id, chunk_text=chunk_text))
        except Exception as exc:
            # schema 실패도 benchmark 결과이므로 전체 실행을 멈추지 않고 실패 chunk로 남긴다.
            responses.append(
                ExtractionResponse(
                    chunk_id=chunk_id,
                    claims=[],
                    usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
                    latency_ms=0,
                    schema_valid=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return responses


def _schema_validation_rate(
    responses: Iterable[ExtractionResponse], chunks: Mapping[str, str]
) -> float:
    response_list = list(responses)
    if len(response_list) != len(chunks):
        return 0.0
    if {response.chunk_id for response in response_list} != set(chunks):
        return 0.0
    valid_count = sum(1 for response in response_list if response.schema_valid)
    return valid_count / len(chunks)


def _response_cost(response: ExtractionResponse, *, pricing: Pricing) -> float:
    input_cost = response.usage.prompt_tokens * pricing.input_per_1m_tokens_usd / 1_000_000
    output_cost = (
        response.usage.completion_tokens * pricing.output_per_1m_tokens_usd / 1_000_000
    )
    return input_cost + output_cost


def _cost_per_chunk(
    responses: Iterable[ExtractionResponse], *, pricing: Pricing, chunk_count: int
) -> float:
    response_list = list(responses)
    total_cost = sum(_response_cost(response, pricing=pricing) for response in response_list)
    return _ratio_float(total_cost, chunk_count)


def _latency_ms_p50(responses: Iterable[ExtractionResponse]) -> float:
    valid_latencies = [
        response.latency_ms
        for response in responses
        if response.schema_valid and response.latency_ms > 0
    ]
    if not valid_latencies:
        return 0.0
    return float(median(valid_latencies))


def _result_payload(
    *,
    provider: ProviderName,
    model: str,
    responses: list[ExtractionResponse],
    scorecard: Scorecard,
    chunk_count: int,
    schema_validation_rate: float,
    total_cost: float,
    cost_per_chunk: float,
    latency_ms_p50: float,
) -> dict[str, object]:
    return {
        "provider": provider,
        "model": model,
        "prompt_version": EXTRACTION_PROMPT_VERSION,
        "chunk_count": chunk_count,
        "schema_validation_rate": schema_validation_rate,
        "grounded_precision": scorecard.grounded_precision,
        "coverage_recall": scorecard.coverage_recall,
        "primary_coverage_recall": scorecard.primary_coverage_recall,
        "numeric_grounding_rate": scorecard.numeric_grounding_rate,
        "direction_accuracy": scorecard.direction_accuracy,
        "magnitude_accuracy": scorecard.magnitude_accuracy,
        "subject_accuracy": scorecard.subject_accuracy,
        "redundancy_rate": scorecard.redundancy_rate,
        "true_positives": scorecard.true_positives,
        "false_negatives": scorecard.false_negatives,
        "total_claims": scorecard.total_claims,
        "grounded_claims": scorecard.grounded_claims,
        "total_cost_usd": total_cost,
        "cost_per_chunk_usd": cost_per_chunk,
        "latency_ms_p50": latency_ms_p50,
        "responses": [
            {
                "chunk_id": response.chunk_id,
                "latency_ms": response.latency_ms,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                },
                "claims": [claim.model_dump(mode="json") for claim in response.claims],
                "error": response.error,
                "schema_valid": response.schema_valid,
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


def _default_model(provider: ProviderName) -> str:
    if provider == "deepseek":
        return DEEPSEEK_MODEL
    return CLAUDE_HAIKU_MODEL


def _client_for_provider(
    *, provider: ProviderName, model: str
) -> DeepSeekExtractionClient | AnthropicExtractionClient:
    # DeepSeek 기본 경로를 유지하면서 Haiku 실험만 provider 옵션으로 분기한다.
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


def _ratio_float(numerator: float, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


if __name__ == "__main__":
    main()
