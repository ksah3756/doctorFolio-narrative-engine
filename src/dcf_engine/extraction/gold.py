"""Fact-level gold labels for extraction benchmark redesign."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dcf_engine.claim import (
    ClaimDirection,
    ClaimNature,
    ClaimSubject,
    MacroVariable,
    MagnitudeQualifier,
)

type NumericUnit = Literal[
    "USD_BN",
    "USD_MN",
    "PCT",
    "PCT_POINT",
    "SHARES_MN",
    "USD_PER_SHARE",
    "RATIO",
    "COUNT",
]
type NumericPeriod = Literal["current", "prior", "change", "change_pct"]
type MagnitudeBasis = Literal["numeric", "qualitative"]
type Salience = Literal["primary", "secondary"]


class SourceFiling(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    form: str
    accession: str
    filing_date: str
    period_end: str
    url: str


class NumericFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    value: float
    unit: NumericUnit
    period: NumericPeriod


class FactPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: str
    prior: str | None


class MagnitudeBand(BaseModel):
    model_config = ConfigDict(frozen=True)

    min: float
    max: float | None


class MagnitudeBands(BaseModel):
    model_config = ConfigDict(frozen=True)

    basis: str
    WEAK: MagnitudeBand
    MODERATE: MagnitudeBand
    STRONG: MagnitudeBand
    EXTREME: MagnitudeBand

    @classmethod
    def default(cls) -> MagnitudeBands:
        return cls(
            basis="abs_relative_pct_change",
            WEAK=MagnitudeBand(min=0, max=10),
            MODERATE=MagnitudeBand(min=10, max=30),
            STRONG=MagnitudeBand(min=30, max=70),
            EXTREME=MagnitudeBand(min=70, max=None),
        )


class GoldFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    canonical_statement: str
    evidence_span: str
    allowed_subjects: list[ClaimSubject] = Field(min_length=1)
    direction: ClaimDirection
    magnitude_qualifier: MagnitudeQualifier
    magnitude_basis: MagnitudeBasis
    acceptable_natures: list[ClaimNature] = Field(min_length=1)
    period: FactPeriod
    numeric_facts: list[NumericFact]
    macro_variable: MacroVariable | None
    salience: Salience

    @model_validator(mode="after")
    def validate_fact_invariants(self) -> GoldFact:
        if len(set(self.allowed_subjects)) != len(self.allowed_subjects):
            raise ValueError("allowed_subjects must not contain duplicates")
        if len(set(self.acceptable_natures)) != len(self.acceptable_natures):
            raise ValueError("acceptable_natures must not contain duplicates")
        has_macro_subject = "MACRO_EXPOSURE" in self.allowed_subjects
        if has_macro_subject != (self.macro_variable is not None):
            raise ValueError("MACRO_EXPOSURE and macro_variable must appear together")
        if self.magnitude_basis == "numeric" and not any(
            numeric_fact.period == "change_pct" for numeric_fact in self.numeric_facts
        ):
            raise ValueError("numeric magnitude facts must include a change_pct value")
        return self


class GoldFactSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    label_status: str
    source_filing: SourceFiling
    magnitude_bands: MagnitudeBands
    labeling_rule: str
    facts_by_chunk: dict[str, list[GoldFact]]


def load_gold_facts(path: Path) -> GoldFactSet:
    return GoldFactSet.model_validate_json(path.read_text())


def band_for_pct(bands: MagnitudeBands, pct: float) -> MagnitudeQualifier:
    value = abs(pct)
    # 밴드 경계는 gold와 evaluator가 같은 기준을 쓰도록 여기서 단일화한다.
    for name in ("WEAK", "MODERATE", "STRONG", "EXTREME"):
        band = getattr(bands, name)
        if value >= band.min and (band.max is None or value < band.max):
            return name
    raise ValueError(f"pct {pct} does not fit configured magnitude bands")
