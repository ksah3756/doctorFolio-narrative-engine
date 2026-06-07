"""Precision and recall evaluation for extracted Claim objects."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

from dcf_engine.claim import Claim

type ClaimRecord = Claim | Mapping[str, object]
type ClaimMatchKey = tuple[str, str, str, str]


class SourceFiling(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    form: str
    accession: str
    filing_date: str
    period_end: str
    url: str


class GoldLabels(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    label_status: str
    source_filing: SourceFiling
    labeling_rule: str
    claims_by_chunk: dict[str, list[Claim]]


@dataclass(frozen=True)
class EvaluationMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float


def load_gold_labels(path: Path) -> GoldLabels:
    return GoldLabels.model_validate_json(path.read_text())


def evaluate_extraction(
    *,
    expected: Sequence[ClaimRecord],
    actual: Sequence[ClaimRecord],
    penalize_extra_claims: bool = True,
) -> EvaluationMetrics:
    expected_counts = _count_keys(expected)
    actual_counts = _count_keys(actual)
    all_keys = set(expected_counts) | set(actual_counts)
    true_positives = sum(
        min(expected_counts.get(key, 0), actual_counts.get(key, 0)) for key in all_keys
    )
    # draft gold는 완전 라벨셋이 아니므로, 발견된 추가 claim을 모델 오류로 단정하지 않는다.
    false_positives = (
        sum(max(actual_counts.get(key, 0) - expected_counts.get(key, 0), 0) for key in all_keys)
        if penalize_extra_claims
        else 0
    )
    false_negatives = sum(
        max(expected_counts.get(key, 0) - actual_counts.get(key, 0), 0) for key in all_keys
    )
    recall = _ratio(true_positives, true_positives + false_negatives)
    precision = (
        _ratio(true_positives, true_positives + false_positives)
        if penalize_extra_claims
        else recall
    )
    return EvaluationMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
    )


def numeric_consistency_rate(claims: Sequence[Claim]) -> float:
    return _ratio(
        sum(1 for claim in claims if claim.extraction_quality.numeric_consistency),
        len(claims),
    )


def _count_keys(claims: Sequence[ClaimRecord]) -> dict[ClaimMatchKey, int]:
    counts: dict[ClaimMatchKey, int] = {}
    for claim in claims:
        key = _claim_key(claim)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _claim_key(claim: ClaimRecord) -> ClaimMatchKey:
    if isinstance(claim, Claim):
        return (
            claim.chunk_ref,
            claim.claim_subject,
            claim.direction,
            claim.magnitude_qualifier,
        )
    return (
        _string_field(claim, "chunk_ref"),
        _string_field(claim, "claim_subject"),
        _string_field(claim, "direction"),
        _string_field(claim, "magnitude_qualifier"),
    )


def _string_field(claim: Mapping[str, object], field: str) -> str:
    value = claim.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def read_json_object(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, object], data)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator
