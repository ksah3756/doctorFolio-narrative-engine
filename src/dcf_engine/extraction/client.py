"""LLM clients for Claim extraction."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from anthropic import Anthropic
from anthropic.types import TextBlock
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from dcf_engine.claim import Claim
from dcf_engine.extraction.prompt import EXTRACTION_SYSTEM_PROMPT, build_user_prompt
from dcf_engine.narrative import ClaimModality

DEEPSEEK_BASE_URL: Final = "https://api.deepseek.com"
DEEPSEEK_MODEL: Final = "deepseek-v4-flash"
CLAUDE_HAIKU_MODEL: Final = "claude-haiku-4-5-20251001"
JSON_FENCE_RE: Final = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class ExtractionResponse:
    chunk_id: str
    claims: list[Claim]
    usage: TokenUsage
    latency_ms: int
    claim_modalities: dict[str, ClaimModality] | None = None
    schema_valid: bool = True
    error: str | None = None


class ExtractionPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    claims: list[Claim]
    claim_modalities: dict[str, ClaimModality]

    @model_validator(mode="after")
    def modalities_match_claims(self) -> ExtractionPayload:
        claim_ids = {claim.claim_id for claim in self.claims}
        modality_ids = set(self.claim_modalities)
        if modality_ids != claim_ids:
            raise ValueError("claim_modalities must contain exactly one label per claim_id")
        return self


class DeepSeekExtractionClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        max_attempts: int = 3,
    ) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if key is None:
            raise RuntimeError("DEEPSEEK_API_KEY is required for live extraction")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._client = OpenAI(api_key=key, base_url=base_url)
        self._model = model
        self._max_attempts = max_attempts

    def extract_claims(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        last_error: Exception | None = None
        # 외부 LLM 호출은 일시 실패가 흔하므로 같은 입력으로 짧게 재시도한다.
        for _attempt in range(self._max_attempts):
            try:
                return self._extract_once(chunk_id=chunk_id, chunk_text=chunk_text)
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable extraction retry state")

    def _extract_once(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        started = time.monotonic()
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(chunk_id=chunk_id, chunk_text=chunk_text),
                },
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=4096,
            extra_body={"thinking": {"type": "disabled"}},
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        content = response.choices[0].message.content
        if content is None:
            raise ValueError(f"DeepSeek returned empty content for {chunk_id}")
        usage = response.usage
        token_usage = TokenUsage(
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
        )
        try:
            payload = _extraction_payload_from_content(content)
        except Exception as exc:
            # 응답 생성 이후 schema만 실패한 경우에도 실제 사용량/지연은 benchmark에 반영한다.
            return ExtractionResponse(
                chunk_id=chunk_id,
                claims=[],
                usage=token_usage,
                latency_ms=elapsed_ms,
                schema_valid=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return ExtractionResponse(
            chunk_id=chunk_id,
            claims=payload.claims,
            usage=token_usage,
            latency_ms=elapsed_ms,
            claim_modalities=payload.claim_modalities,
        )


class AnthropicExtractionClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = CLAUDE_HAIKU_MODEL,
        max_attempts: int = 3,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if key is None:
            raise RuntimeError("ANTHROPIC_API_KEY is required for live extraction")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._client = Anthropic(api_key=key)
        self._model = model
        self._max_attempts = max_attempts

    def extract_claims(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        last_error: Exception | None = None
        # Anthropic도 동일한 retry 계약을 유지해 provider별 실패 처리를 맞춘다.
        for _attempt in range(self._max_attempts):
            try:
                return self._extract_once(chunk_id=chunk_id, chunk_text=chunk_text)
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable extraction retry state")

    def _extract_once(self, *, chunk_id: str, chunk_text: str) -> ExtractionResponse:
        started = time.monotonic()
        response = self._client.messages.create(
            model=self._model,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_user_prompt(chunk_id=chunk_id, chunk_text=chunk_text),
                }
            ],
            temperature=0,
            max_tokens=4096,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        token_usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )
        try:
            payload = _extraction_payload_from_content(_anthropic_text(response.content))
        except Exception as exc:
            # Claude가 schema 제약만 어긴 경우에도 토큰/지연 실측값은 보존한다.
            return ExtractionResponse(
                chunk_id=chunk_id,
                claims=[],
                usage=token_usage,
                latency_ms=elapsed_ms,
                schema_valid=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return ExtractionResponse(
            chunk_id=chunk_id,
            claims=payload.claims,
            usage=token_usage,
            latency_ms=elapsed_ms,
            claim_modalities=payload.claim_modalities,
        )


def _claims_from_content(content: str) -> list[Claim]:
    return _extraction_payload_from_content(content).claims


def _extraction_payload_from_content(content: str) -> ExtractionPayload:
    data = json.loads(_json_object_text(content))
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    claims = data.get("claims")
    if not isinstance(claims, list):
        raise ValueError("LLM response must contain a claims list")
    try:
        return ExtractionPayload.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid extraction payload: {exc}") from exc


def _json_object_text(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("{"):
        return stripped
    fence_match = JSON_FENCE_RE.search(stripped)
    if fence_match is not None:
        return fence_match.group(1)
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            _, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        # Claude가 앞뒤 설명을 붙여도 첫 JSON object만 평가 대상으로 사용한다.
        return stripped[index : index + end]
    return stripped


def _anthropic_text(blocks: Sequence[object]) -> str:
    # Messages API는 여러 block을 돌려주므로 텍스트 block만 합쳐 JSON 파서에 넘긴다.
    text = "".join(block.text for block in blocks if isinstance(block, TextBlock))
    if not text:
        raise ValueError("Anthropic returned empty content")
    return text
