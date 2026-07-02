"""Audit-only explanation records for Type-1 narrative tension axes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Final

from dcf_engine.claim import Claim, SourceRef
from dcf_engine.narrative_axes import ClaimAssumptionPull, NarrativeAxis

EXPLANATION_PULL_TOLERANCE: Final = 1e-12

type FactAnchorKey = tuple[str, str, date, str, str, float]


@dataclass(frozen=True)
class Type1FactAnchor:
    claim_text: str
    chunk_ref: str
    published_date: date
    source_ref: SourceRef


@dataclass(frozen=True)
class Type1FactEvidence:
    claim_id: str
    claim_text: str
    chunk_ref: str
    published_date: date
    source_ref: SourceRef
    pull: float


@dataclass(frozen=True)
class Type1FactExplanationPair:
    axis_index: int
    assumption_id: str
    fact_anchor: Type1FactAnchor
    positive_evidence: Type1FactEvidence
    negative_evidence: Type1FactEvidence


def build_type1_fact_explanations(
    *,
    claims: Sequence[Claim],
    pulls: Sequence[ClaimAssumptionPull],
    axes: Sequence[NarrativeAxis],
) -> tuple[Type1FactExplanationPair, ...]:
    """Explain Type-1 axes with same-fact, opposite-pull claim pairs.

    This is an audit layer only: it consumes already-built Type-1 pull rows and
    axes, then returns deterministic records without mutating or regenerating
    the Type-1 axis/candidate inputs.
    """

    if not claims or not pulls or not axes:
        return ()

    claims_by_id = _claims_by_id(claims)
    pull_by_cell = _pulls_by_cell(pulls)
    rows: list[Type1FactExplanationPair] = []

    for axis in sorted(axes, key=lambda item: item.axis_index):
        for assumption_id in sorted(axis.loadings):
            groups = _evidence_groups_for_assumption(
                assumption_id=assumption_id,
                claims_by_id=claims_by_id,
                pull_by_cell=pull_by_cell,
            )
            for anchor_key in sorted(groups):
                anchor, evidence = groups[anchor_key]
                positive = tuple(
                    sorted(
                        (
                            item
                            for item in evidence
                            if item.pull > EXPLANATION_PULL_TOLERANCE
                        ),
                        key=_evidence_sort_key,
                    )
                )
                negative = tuple(
                    sorted(
                        (
                            item
                            for item in evidence
                            if item.pull < -EXPLANATION_PULL_TOLERANCE
                        ),
                        key=_evidence_sort_key,
                    )
                )
                for positive_item in positive:
                    for negative_item in negative:
                        rows.append(
                            Type1FactExplanationPair(
                                axis_index=axis.axis_index,
                                assumption_id=assumption_id,
                                fact_anchor=anchor,
                                positive_evidence=positive_item,
                                negative_evidence=negative_item,
                            )
                        )

    return tuple(rows)


def _claims_by_id(claims: Sequence[Claim]) -> dict[str, Claim]:
    claims_by_id: dict[str, Claim] = {}
    for claim in claims:
        if claim.claim_id in claims_by_id:
            raise ValueError(f"duplicate claim id: {claim.claim_id}")
        claims_by_id[claim.claim_id] = claim
    return claims_by_id


def _pulls_by_cell(
    pulls: Sequence[ClaimAssumptionPull],
) -> dict[tuple[str, str], ClaimAssumptionPull]:
    pull_by_cell: dict[tuple[str, str], ClaimAssumptionPull] = {}
    for pull in pulls:
        if not isfinite(pull.pull):
            raise ValueError("claim-assumption pull values must be finite")
        cell = (pull.claim_id, pull.assumption_id)
        if cell in pull_by_cell:
            raise ValueError("claim-assumption pulls must be unique per claim and assumption")
        pull_by_cell[cell] = pull
    return pull_by_cell


def _evidence_groups_for_assumption(
    *,
    assumption_id: str,
    claims_by_id: dict[str, Claim],
    pull_by_cell: dict[tuple[str, str], ClaimAssumptionPull],
) -> dict[FactAnchorKey, tuple[Type1FactAnchor, tuple[Type1FactEvidence, ...]]]:
    mutable_groups: dict[FactAnchorKey, list[Type1FactEvidence]] = defaultdict(list)
    anchors: dict[FactAnchorKey, Type1FactAnchor] = {}

    for (claim_id, pull_assumption_id), pull in sorted(pull_by_cell.items()):
        if pull_assumption_id != assumption_id:
            continue
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        anchor = _fact_anchor(claim)
        if anchor is None:
            continue
        anchor_key = _fact_anchor_key(anchor)
        anchors[anchor_key] = anchor
        mutable_groups[anchor_key].append(
            Type1FactEvidence(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                chunk_ref=claim.chunk_ref,
                published_date=claim.published_date,
                source_ref=claim.source_ref,
                pull=pull.pull,
            )
        )

    return {
        anchor_key: (anchors[anchor_key], tuple(evidence))
        for anchor_key, evidence in mutable_groups.items()
    }


def _fact_anchor(claim: Claim) -> Type1FactAnchor | None:
    if not claim.claim_text.strip() or not claim.chunk_ref.strip():
        return None
    return Type1FactAnchor(
        claim_text=claim.claim_text,
        chunk_ref=claim.chunk_ref,
        published_date=claim.published_date,
        source_ref=claim.source_ref,
    )


def _fact_anchor_key(anchor: Type1FactAnchor) -> FactAnchorKey:
    return (
        anchor.claim_text,
        anchor.chunk_ref,
        anchor.published_date,
        anchor.source_ref.discovery_channel,
        anchor.source_ref.content_source,
        anchor.source_ref.source_reliability,
    )


def _evidence_sort_key(evidence: Type1FactEvidence) -> tuple[str, str, date, str, str, float]:
    return (
        evidence.claim_id,
        evidence.chunk_ref,
        evidence.published_date,
        evidence.source_ref.discovery_channel,
        evidence.source_ref.content_source,
        evidence.pull,
    )
