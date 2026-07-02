"""Deterministic Type-1 fact-coreference explanation rows."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

type FactCoreferenceClaimKind = Literal[
    "FACT",
    "SHARED_OBSERVATION",
    "INTERPRETATION",
    "PROJECTION",
]

ALL_CLAIM_KINDS: Final[frozenset[FactCoreferenceClaimKind]] = frozenset(
    {
        "FACT",
        "SHARED_OBSERVATION",
        "INTERPRETATION",
        "PROJECTION",
    }
)
EXPLANATION_CLAIM_KINDS: Final[frozenset[FactCoreferenceClaimKind]] = frozenset(
    {"INTERPRETATION", "PROJECTION"}
)


@dataclass(frozen=True, order=True)
class FactCoreferenceFactKey:
    source_id: str
    evidence_span: str
    metric_id: str
    period: str


@dataclass(frozen=True)
class FactCoreferenceEvidence:
    claim_id: str
    claim_kind: FactCoreferenceClaimKind
    source_id: str
    evidence_span: str
    metric_id: str
    period: str
    assumption_pulls: Mapping[str, float]


@dataclass(frozen=True)
class FactCoreferenceOpposition:
    assumption_id: str
    positive_claim_ids: tuple[str, ...]
    negative_claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class FactCoreferenceExplanation:
    group_id: str
    fact_key: FactCoreferenceFactKey
    claim_ids: tuple[str, ...]
    opposing_assumptions: tuple[FactCoreferenceOpposition, ...]


def build_fact_coreference_explanations(
    evidence: Sequence[FactCoreferenceEvidence],
) -> tuple[FactCoreferenceExplanation, ...]:
    """Group same-fact interpretation/projection rows with opposing assumption pulls."""

    _validate_evidence(evidence)
    groups: dict[FactCoreferenceFactKey, list[FactCoreferenceEvidence]] = defaultdict(list)
    for item in evidence:
        if item.claim_kind not in EXPLANATION_CLAIM_KINDS:
            continue
        groups[_fact_key(item)].append(item)

    explanations: list[FactCoreferenceExplanation] = []
    for fact_key in sorted(groups):
        opposing_assumptions = _opposing_assumptions(groups[fact_key])
        if not opposing_assumptions:
            continue
        claim_ids = tuple(
            sorted(
                {
                    claim_id
                    for opposition in opposing_assumptions
                    for claim_id in (
                        opposition.positive_claim_ids + opposition.negative_claim_ids
                    )
                }
            )
        )
        explanations.append(
            FactCoreferenceExplanation(
                group_id=_group_id(fact_key),
                fact_key=fact_key,
                claim_ids=claim_ids,
                opposing_assumptions=opposing_assumptions,
            )
        )

    return tuple(explanations)


def _validate_evidence(evidence: Sequence[FactCoreferenceEvidence]) -> None:
    if not evidence:
        raise ValueError("fact-coreference evidence must be non-empty")

    claim_ids = [item.claim_id for item in evidence]
    if any(not claim_id.strip() for claim_id in claim_ids):
        raise ValueError("claim_id must be non-empty")
    duplicate_claim_ids = {
        claim_id for claim_id in claim_ids if claim_ids.count(claim_id) > 1
    }
    if duplicate_claim_ids:
        ordered_duplicates = ", ".join(sorted(duplicate_claim_ids))
        raise ValueError(f"duplicate claim ids: {ordered_duplicates}")

    for item in evidence:
        if item.claim_kind not in ALL_CLAIM_KINDS:
            raise ValueError("claim_kind must be a supported fact-coreference kind")
        _validate_fact_key(_fact_key(item))
        _validate_assumption_pulls(item)


def _validate_fact_key(fact_key: FactCoreferenceFactKey) -> None:
    if not fact_key.source_id.strip():
        raise ValueError("source_id must be non-empty")
    if not fact_key.evidence_span.strip():
        raise ValueError("evidence_span must be non-empty")
    if not fact_key.metric_id.strip():
        raise ValueError("metric_id must be non-empty")
    if not fact_key.period.strip():
        raise ValueError("period must be non-empty")


def _validate_assumption_pulls(item: FactCoreferenceEvidence) -> None:
    if item.claim_kind in EXPLANATION_CLAIM_KINDS and not item.assumption_pulls:
        raise ValueError("assumption_pulls must be non-empty for explanation claims")

    for assumption_id, pull in item.assumption_pulls.items():
        if not assumption_id.strip():
            raise ValueError("assumption_id must be non-empty")
        try:
            finite = math.isfinite(pull)
        except TypeError as error:
            raise ValueError("assumption pull values must be finite") from error
        if not finite:
            raise ValueError("assumption pull values must be finite")


def _fact_key(item: FactCoreferenceEvidence) -> FactCoreferenceFactKey:
    return FactCoreferenceFactKey(
        source_id=item.source_id,
        evidence_span=item.evidence_span,
        metric_id=item.metric_id,
        period=item.period,
    )


def _opposing_assumptions(
    evidence: Sequence[FactCoreferenceEvidence],
) -> tuple[FactCoreferenceOpposition, ...]:
    assumption_ids = sorted(
        {assumption_id for item in evidence for assumption_id in item.assumption_pulls}
    )
    oppositions: list[FactCoreferenceOpposition] = []
    for assumption_id in assumption_ids:
        positive_claim_ids = tuple(
            sorted(
                item.claim_id
                for item in evidence
                if item.assumption_pulls.get(assumption_id, 0.0) > 0.0
            )
        )
        negative_claim_ids = tuple(
            sorted(
                item.claim_id
                for item in evidence
                if item.assumption_pulls.get(assumption_id, 0.0) < 0.0
            )
        )
        if positive_claim_ids and negative_claim_ids:
            oppositions.append(
                FactCoreferenceOpposition(
                    assumption_id=assumption_id,
                    positive_claim_ids=positive_claim_ids,
                    negative_claim_ids=negative_claim_ids,
                )
            )
    return tuple(oppositions)


def _group_id(fact_key: FactCoreferenceFactKey) -> str:
    payload = json.dumps(
        {
            "source_id": fact_key.source_id,
            "evidence_span": fact_key.evidence_span,
            "metric_id": fact_key.metric_id,
            "period": fact_key.period,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"fact-coref-{digest}"
