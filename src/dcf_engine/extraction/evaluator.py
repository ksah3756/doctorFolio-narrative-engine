"""Fact-grounded scorecard evaluation for extracted Claim objects."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]

from dcf_engine.claim import Claim, MagnitudeQualifier
from dcf_engine.extraction.client import ExtractionResponse
from dcf_engine.extraction.gold import GoldFact, GoldFactSet

NUMERIC_WEIGHT: Final = 0.6
TEXT_WEIGHT: Final = 0.4
MATCH_THRESHOLD: Final = 0.45
GROUNDING_TEXT_THRESHOLD: Final = 0.30
NUMBER_RE: Final = re.compile(r"\$?\b\d[\d,]*(?:\.\d+)?%?")
TOKEN_RE: Final = re.compile(r"[a-z0-9]+")
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "the",
        "their",
        "this",
        "to",
        "was",
        "we",
        "were",
        "with",
    }
)
MAGNITUDE_ORDER: Final[dict[MagnitudeQualifier, int]] = {
    "WEAK": 0,
    "MODERATE": 1,
    "STRONG": 2,
    "EXTREME": 3,
}


@dataclass(frozen=True)
class MatchedPair:
    claim: Claim
    fact: GoldFact
    score: float


@dataclass(frozen=True)
class Scorecard:
    true_positives: int
    false_negatives: int
    total_claims: int
    grounded_claims: int
    coverage_recall: float
    primary_coverage_recall: float
    grounded_precision: float
    numeric_grounding_rate: float
    direction_accuracy: float
    magnitude_accuracy: float
    subject_accuracy: float
    redundancy_rate: float


def score_extraction(
    *,
    gold: GoldFactSet,
    responses: Sequence[ExtractionResponse],
    chunk_texts: Mapping[str, str],
) -> Scorecard:
    grounded_claims_by_chunk: dict[str, list[Claim]] = {}
    matched_pairs: list[MatchedPair] = []
    total_facts = sum(len(facts) for facts in gold.facts_by_chunk.values())
    total_primary_facts = sum(
        1 for facts in gold.facts_by_chunk.values() for fact in facts if fact.salience == "primary"
    )
    total_claims = sum(len(response.claims) for response in responses)
    grounded_claim_count = 0
    numeric_claim_count = 0
    numeric_grounded_count = 0

    for response in responses:
        chunk_text = chunk_texts.get(response.chunk_id, "")
        for claim in response.claims:
            claim_numbers = normalize_numbers(claim.claim_text)
            if claim_numbers:
                numeric_claim_count += 1
                if _numbers_are_grounded(claim_numbers, normalize_numbers(chunk_text)):
                    numeric_grounded_count += 1
            if is_grounded(claim, chunk_text):
                grounded_claim_count += 1
                grounded_claims_by_chunk.setdefault(response.chunk_id, []).append(claim)

    for chunk_id, facts in gold.facts_by_chunk.items():
        # chunk별 assignment로 전역 tuple collision이 recall을 부풀리지 못하게 한다.
        matched_pairs.extend(
            match_claims_to_facts(grounded_claims_by_chunk.get(chunk_id, []), facts)
        )

    true_positives = len(matched_pairs)
    false_negatives = total_facts - true_positives
    primary_true_positives = sum(1 for pair in matched_pairs if pair.fact.salience == "primary")

    return Scorecard(
        true_positives=true_positives,
        false_negatives=false_negatives,
        total_claims=total_claims,
        grounded_claims=grounded_claim_count,
        coverage_recall=_ratio(true_positives, total_facts),
        primary_coverage_recall=_ratio(primary_true_positives, total_primary_facts),
        grounded_precision=_ratio(grounded_claim_count, total_claims),
        numeric_grounding_rate=_ratio(numeric_grounded_count, numeric_claim_count),
        direction_accuracy=_ratio(
            sum(1 for pair in matched_pairs if pair.claim.direction == pair.fact.direction),
            true_positives,
        ),
        magnitude_accuracy=_ratio(
            sum(1 for pair in matched_pairs if _magnitude_matches(pair.claim, pair.fact)),
            true_positives,
        ),
        subject_accuracy=_ratio(
            sum(
                1
                for pair in matched_pairs
                if pair.claim.claim_subject in pair.fact.allowed_subjects
            ),
            true_positives,
        ),
        redundancy_rate=_ratio(grounded_claim_count - true_positives, grounded_claim_count),
    )


def match_claims_to_facts(claims: Sequence[Claim], facts: Sequence[GoldFact]) -> list[MatchedPair]:
    if not claims or not facts:
        return []
    scores = [[match_score(claim, fact) for fact in facts] for claim in claims]
    row_indexes, column_indexes = linear_sum_assignment(
        [[-score for score in row] for row in scores]
    )
    pairs: list[MatchedPair] = []
    for row_index, column_index in zip(row_indexes, column_indexes, strict=True):
        score = scores[row_index][column_index]
        if score >= MATCH_THRESHOLD:
            pairs.append(
                MatchedPair(claim=claims[row_index], fact=facts[column_index], score=score)
            )
    return pairs


def match_score(claim: Claim, fact: GoldFact) -> float:
    fact_numbers = _fact_numbers(fact)
    claim_numbers = normalize_numbers(claim.claim_text)
    numeric_overlap = _numeric_overlap(claim_numbers, fact_numbers)
    text_overlap = _jaccard(
        tokenize(claim.claim_text),
        tokenize(f"{fact.evidence_span} {fact.canonical_statement}"),
    )
    return NUMERIC_WEIGHT * numeric_overlap + TEXT_WEIGHT * text_overlap


def is_grounded(claim: Claim, chunk_text: str) -> bool:
    claim_numbers = normalize_numbers(claim.claim_text)
    if claim_numbers:
        # 숫자 claim은 모든 수치가 같은 chunk에 실제로 있어야 grounded로 본다.
        return _numbers_are_grounded(claim_numbers, normalize_numbers(chunk_text))
    return _jaccard(tokenize(claim.claim_text), tokenize(chunk_text)) >= GROUNDING_TEXT_THRESHOLD


def normalize_numbers(text: str) -> set[float]:
    return {_normalize_number(match.group()) for match in NUMBER_RE.finditer(text)}


def tokenize(text: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS}


def read_json_object(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, object], data)


def _fact_numbers(fact: GoldFact) -> set[float]:
    numbers = {_round_number(numeric_fact.value) for numeric_fact in fact.numeric_facts}
    if numbers:
        return numbers
    return normalize_numbers(f"{fact.evidence_span} {fact.canonical_statement}")


def _numeric_overlap(claim_numbers: set[float], fact_numbers: set[float]) -> float:
    if not fact_numbers:
        return 1.0
    matched = sum(1 for number in fact_numbers if _contains_number(claim_numbers, number))
    return matched / max(1, len(fact_numbers))


def _numbers_are_grounded(claim_numbers: set[float], chunk_numbers: set[float]) -> bool:
    return all(_contains_number(chunk_numbers, claim_number) for claim_number in claim_numbers)


def _contains_number(candidates: set[float], target: float) -> bool:
    return any(abs(candidate - target) <= max(1e-6, abs(target) * 1e-6) for candidate in candidates)


def _normalize_number(value: str) -> float:
    return _round_number(float(value.strip("$%").replace(",", "")))


def _round_number(value: float) -> float:
    return round(value, 3)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def _magnitude_matches(claim: Claim, fact: GoldFact) -> bool:
    if fact.magnitude_basis == "numeric":
        return claim.magnitude_qualifier == fact.magnitude_qualifier
    return (
        abs(
            MAGNITUDE_ORDER[claim.magnitude_qualifier]
            - MAGNITUDE_ORDER[fact.magnitude_qualifier]
        )
        <= 1
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return numerator / denominator
