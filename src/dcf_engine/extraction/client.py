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

from dcf_engine.claim import Claim
from dcf_engine.extraction.prompt import EXTRACTION_SYSTEM_PROMPT, build_user_prompt

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
    schema_valid: bool = True
    error: str | None = None


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
        claims = _claims_from_content(content)
        usage = response.usage
        return ExtractionResponse(
            chunk_id=chunk_id,
            claims=claims,
            usage=TokenUsage(
                prompt_tokens=usage.prompt_tokens if usage is not None else 0,
                completion_tokens=usage.completion_tokens if usage is not None else 0,
            ),
            latency_ms=elapsed_ms,
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
        claims = _claims_from_content(_anthropic_text(response.content))
        return ExtractionResponse(
            chunk_id=chunk_id,
            claims=claims,
            usage=TokenUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            ),
            latency_ms=elapsed_ms,
        )


def _claims_from_content(content: str) -> list[Claim]:
    data = json.loads(_json_object_text(content))
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")
    claims = data.get("claims")
    if not isinstance(claims, list):
        raise ValueError("LLM response must contain a claims list")
    return [Claim.model_validate(claim) for claim in claims]


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
