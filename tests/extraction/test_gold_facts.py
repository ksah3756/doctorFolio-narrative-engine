from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from dcf_engine.claim import ClaimSubject, MacroVariable
from dcf_engine.extraction.gold import (
    FactPeriod,
    GoldFact,
    GoldFactSet,
    MagnitudeBands,
    NumericFact,
    band_for_pct,
    load_gold_facts,
)

ROOT = Path(__file__).resolve().parents[2]
CHUNKS_DIR = ROOT / "data" / "benchmark" / "chunks"
GOLD_FACTS_PATH = ROOT / "data" / "benchmark" / "gold_facts.json"
HEADER_RE = re.compile(r"^#.*$", re.MULTILINE)
NUMBER_RE = re.compile(r"\$?\b\d[\d,]*(?:\.\d+)?%?")


@pytest.fixture(scope="module")
def gold_facts() -> GoldFactSet:
    return load_gold_facts(GOLD_FACTS_PATH)


def test_gold_facts_schema_version_loads(gold_facts: GoldFactSet) -> None:
    assert gold_facts.schema_version == 2


def test_gold_facts_cover_every_benchmark_chunk(gold_facts: GoldFactSet) -> None:
    chunk_ids = {path.stem for path in CHUNKS_DIR.glob("*.txt")}

    assert len(chunk_ids) == 10
    assert set(gold_facts.facts_by_chunk) == chunk_ids
    assert all(gold_facts.facts_by_chunk[chunk_id] for chunk_id in chunk_ids)


def test_gold_fact_ids_are_unique_and_chunk_scoped(gold_facts: GoldFactSet) -> None:
    for chunk_id, facts in gold_facts.facts_by_chunk.items():
        chunk_number = chunk_id.split("-")[1]
        fact_ids = [fact.fact_id for fact in facts]

        assert len(fact_ids) == len(set(fact_ids))
        assert all(fact_id.startswith(f"fact-{chunk_number}-") for fact_id in fact_ids)


def test_gold_facts_are_grounded_in_source_chunks(gold_facts: GoldFactSet) -> None:
    for chunk_id, facts in gold_facts.facts_by_chunk.items():
        chunk_text = _normalized_body(CHUNKS_DIR / f"{chunk_id}.txt")
        for fact in facts:
            assert _normalize(fact.evidence_span) in chunk_text, fact.fact_id


def test_gold_numeric_facts_are_grounded_in_source_chunks(gold_facts: GoldFactSet) -> None:
    for chunk_id, facts in gold_facts.facts_by_chunk.items():
        chunk_numbers = _numbers_in_chunk(CHUNKS_DIR / f"{chunk_id}.txt")
        for fact in facts:
            for numeric_fact in fact.numeric_facts:
                assert numeric_fact.value in chunk_numbers, (
                    fact.fact_id,
                    numeric_fact.metric,
                    numeric_fact.value,
                )


def test_numeric_magnitude_labels_follow_configured_bands(
    gold_facts: GoldFactSet,
) -> None:
    for facts in gold_facts.facts_by_chunk.values():
        for fact in facts:
            if fact.magnitude_basis != "numeric":
                continue
            pct_values = [
                numeric_fact.value
                for numeric_fact in fact.numeric_facts
                if numeric_fact.period == "change_pct"
            ]
            assert pct_values, fact.fact_id
            assert band_for_pct(gold_facts.magnitude_bands, pct_values[0]) == (
                fact.magnitude_qualifier
            )


def test_numeric_direction_matches_current_and_prior_values(
    gold_facts: GoldFactSet,
) -> None:
    for facts in gold_facts.facts_by_chunk.values():
        for fact in facts:
            current_values = [
                _direction_value(numeric_fact)
                for numeric_fact in fact.numeric_facts
                if numeric_fact.period == "current"
            ]
            prior_values = [
                _direction_value(numeric_fact)
                for numeric_fact in fact.numeric_facts
                if numeric_fact.period == "prior"
            ]
            if not current_values or not prior_values:
                continue
            if current_values[0] > prior_values[0]:
                assert fact.direction == "INCREASE", fact.fact_id
            elif current_values[0] < prior_values[0]:
                assert fact.direction == "DECREASE", fact.fact_id
            else:
                assert fact.direction == "NEUTRAL", fact.fact_id


def test_gold_facts_keep_multi_label_subjects_for_known_ambiguous_chunks(
    gold_facts: GoldFactSet,
) -> None:
    assert any(
        len(fact.allowed_subjects) >= 2
        for fact in gold_facts.facts_by_chunk["chunk-01-revenue-overview"]
    )
    assert any(
        len(fact.allowed_subjects) >= 2
        for fact in gold_facts.facts_by_chunk["chunk-04-geographic-mix"]
    )


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (9.99, "WEAK"),
        (10.0, "MODERATE"),
        (30.0, "STRONG"),
        (70.0, "EXTREME"),
    ],
)
def test_band_for_pct_boundaries(pct: float, expected: str) -> None:
    bands = MagnitudeBands.default()

    assert band_for_pct(bands, pct) == expected


def test_macro_exposure_requires_macro_variable() -> None:
    with pytest.raises(ValidationError):
        _fact(allowed_subjects=["MACRO_EXPOSURE"], macro_variable=None)


def test_macro_variable_is_only_valid_for_macro_exposure() -> None:
    with pytest.raises(ValidationError):
        _fact(allowed_subjects=["DEMAND_SIGNAL"], macro_variable="RATE")


def _fact(
    *, allowed_subjects: list[ClaimSubject], macro_variable: MacroVariable | None
) -> GoldFact:
    return GoldFact(
        fact_id="fact-99-01",
        canonical_statement="Interest rates affected interest income.",
        evidence_span="Interest income was $1 million",
        allowed_subjects=allowed_subjects,
        direction="INCREASE",
        magnitude_qualifier="WEAK",
        magnitude_basis="numeric",
        acceptable_natures=["REALIZED"],
        period=FactPeriod(current="Q1 FY2027", prior="Q1 FY2026"),
        numeric_facts=[
            NumericFact(
                metric="interest income",
                value=1.0,
                unit="USD_MN",
                period="change_pct",
            )
        ],
        macro_variable=macro_variable,
        salience="secondary",
    )


def _normalized_body(path: Path) -> str:
    return _normalize(HEADER_RE.sub("", path.read_text()))


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _numbers_in_chunk(path: Path) -> set[float]:
    return {_number_value(match.group()) for match in NUMBER_RE.finditer(path.read_text())}


def _number_value(value: str) -> float:
    return float(value.strip("$%").replace(",", ""))


def _direction_value(numeric_fact: NumericFact) -> float:
    if numeric_fact.unit == "USD_MN":
        return numeric_fact.value / 1_000
    return numeric_fact.value
