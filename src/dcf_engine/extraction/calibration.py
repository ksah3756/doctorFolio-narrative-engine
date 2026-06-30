"""Replay-only extraction stability calibration."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from dcf_engine.claim import ClaimDirection, ClaimSubject
from dcf_engine.extraction.client import ExtractionResponse

DEFAULT_AGREEMENT_THRESHOLD: Final = 0.9
CLAIM_GROUP_RE: Final = re.compile(r"[a-z0-9]+")

type ClaimLabel = tuple[ClaimSubject, ClaimDirection]


@dataclass(frozen=True)
class CalibrationGroup:
    chunk_id: str
    claim_group: str
    valid_repeat_count: int
    agreement_rate: float
    threshold: float
    label_counts: dict[ClaimLabel, int]


@dataclass(frozen=True)
class CalibrationResult:
    passed: bool
    valid_repeat_count: int
    invalid_repeat_count: int
    agreement_rate: float
    threshold: float
    unstable_groups: tuple[CalibrationGroup, ...]


def calibrate_extraction_replay(
    responses: Sequence[ExtractionResponse],
    *,
    threshold: float = DEFAULT_AGREEMENT_THRESHOLD,
) -> CalibrationResult:
    if len(responses) < 2:
        raise ValueError("calibration requires at least two replay responses")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")

    valid_responses = [response for response in responses if response.schema_valid]
    invalid_repeat_count = len(responses) - len(valid_responses)
    if len(valid_responses) < 2:
        raise ValueError("calibration requires at least two schema-valid replay responses")

    grouped_labels: dict[tuple[str, str], Counter[ClaimLabel]] = {}
    valid_counts_by_chunk = Counter(response.chunk_id for response in valid_responses)
    for response in valid_responses:
        for claim in response.claims:
            claim_group = _claim_group_key(claim.claim_text)
            label: ClaimLabel = (claim.claim_subject, claim.direction)
            grouped_labels.setdefault((response.chunk_id, claim_group), Counter())[label] += 1

    if not grouped_labels:
        raise ValueError("calibration requires at least one claim in schema-valid replay responses")

    groups = tuple(
        _calibration_group(
            chunk_id=chunk_id,
            claim_group=claim_group,
            label_counts=label_counts,
            valid_repeat_count=valid_counts_by_chunk[chunk_id],
            threshold=threshold,
        )
        for (chunk_id, claim_group), label_counts in sorted(grouped_labels.items())
    )
    unstable_groups = tuple(group for group in groups if group.agreement_rate < threshold)
    agreement_rate = _overall_agreement_rate(groups)

    return CalibrationResult(
        passed=not unstable_groups and agreement_rate >= threshold,
        valid_repeat_count=len(valid_responses),
        invalid_repeat_count=invalid_repeat_count,
        agreement_rate=agreement_rate,
        threshold=threshold,
        unstable_groups=unstable_groups,
    )


def _calibration_group(
    *,
    chunk_id: str,
    claim_group: str,
    label_counts: Counter[ClaimLabel],
    valid_repeat_count: int,
    threshold: float,
) -> CalibrationGroup:
    dominant_count = max(label_counts.values())
    return CalibrationGroup(
        chunk_id=chunk_id,
        claim_group=claim_group,
        valid_repeat_count=valid_repeat_count,
        agreement_rate=dominant_count / valid_repeat_count,
        threshold=threshold,
        label_counts=dict(sorted(label_counts.items())),
    )


def _claim_group_key(claim_text: str) -> str:
    # Claim IDs can vary between repeats, so use normalized claim text as the replay key.
    return " ".join(CLAIM_GROUP_RE.findall(claim_text.lower()))


def _overall_agreement_rate(groups: Sequence[CalibrationGroup]) -> float:
    stable_count = sum(max(group.label_counts.values()) for group in groups)
    total_count = sum(group.valid_repeat_count for group in groups)
    return stable_count / total_count
