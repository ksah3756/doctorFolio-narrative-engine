"""Claim data models and extraction validation helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

type ClaimSubject = Literal[
    "DEMAND_SIGNAL",
    "SUPPLY_SIGNAL",
    "PRICING_SIGNAL",
    "COST_SIGNAL",
    "CAPITAL_ALLOCATION",
    "COMPETITIVE_POSITION",
    "MARKET_STRUCTURE",
    "FINANCIAL_HEALTH",
    "GOVERNANCE",
    "MACRO_EXPOSURE",
    "CAPITAL_STRUCTURE",
]
type ClaimNature = Literal["REALIZED", "GUIDANCE", "EXTERNAL", "STRUCTURAL", "RISK_FLAG"]
type ClaimDirection = Literal["INCREASE", "DECREASE", "NEUTRAL"]
type MagnitudeQualifier = Literal["WEAK", "MODERATE", "STRONG", "EXTREME"]
type MacroVariable = Literal["RATE", "INFLATION", "FX", "COMMODITY"]
type CapitalStructureInstrument = Literal[
    "corporate_bond",
    "bank_loan",
    "lease",
    "stock_option",
    "minority_stake",
    "equity_issuance",
    "treasury_stock",
]
type DiscoveryChannel = Literal[
    "naver_news",
    "google_news",
    "rss_aggregator",
    "direct",
    "edgar_api",
    "dart_api",
]

CAPITAL_STRUCTURE_INSTRUMENT_ADAPTER: Final[
    TypeAdapter[CapitalStructureInstrument]
] = TypeAdapter(CapitalStructureInstrument)

SOURCE_RELIABILITY: Final[dict[str, float]] = {
    "10-K": 0.95,
    "10-Q": 0.95,
    "8-K": 0.90,
    "earnings_call": 0.90,
    "press_release": 0.80,
    "reuters": 0.70,
    "cnbc": 0.65,
    "ap": 0.70,
    "analyst_report_full": 0.75,
    "analyst_report_summary": 0.55,
    "blog": 0.30,
    "dart_annual": 0.95,
    "dart_quarterly": 0.95,
    "tentative_earnings": 0.90,
    "krx_disclosure": 0.90,
    "ir_material": 0.75,
    "hankyung": 0.70,
    "maekyung": 0.70,
    "yonhap_infomax": 0.70,
    "kr_news_tier2": 0.55,
    "community": 0.25,
    "fred": 0.95,
    "ecos": 0.95,
    "kosis": 0.90,
}
INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"ignore\s+previous\s+instructions\.?\s*", re.IGNORECASE),
    re.compile(r"system\s*:\s*.*", re.IGNORECASE),
    re.compile(r"set\s+(?:role|instruction|developer)\s*=.*", re.IGNORECASE),
)


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    discovery_channel: DiscoveryChannel
    content_source: str
    source_reliability: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def reliability_matches_source(self) -> SourceRef:
        expected = SOURCE_RELIABILITY.get(self.content_source)
        if expected is not None and abs(self.source_reliability - expected) > 1e-9:
            raise ValueError("source_reliability must match content_source")
        return self


class ExtractionQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    verbatim_overlap: float = Field(ge=0.0, le=1.0)
    numeric_consistency: bool
    temporal_consistency: bool
    entity_consistency: bool


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    claim_text: str
    claim_subject: ClaimSubject
    claim_nature: ClaimNature
    direction: ClaimDirection
    magnitude_qualifier: MagnitudeQualifier
    macro_variable: MacroVariable | None = None
    instrument_type: str | None = None
    extraction_quality: ExtractionQuality
    source_ref: SourceRef
    chunk_ref: str
    published_date: date

    @model_validator(mode="before")
    @classmethod
    def reject_persisted_modality(cls, data: object) -> object:
        if isinstance(data, Mapping) and "modality" in data:
            raise ValueError("modality is temporary extraction metadata, not a Claim field")
        return data

    @field_validator("claim_text")
    @classmethod
    def sanitize_text(cls, value: str) -> str:
        return sanitize_claim_text(value)

    @model_validator(mode="after")
    def quality_gate(self) -> Claim:
        quality = self.extraction_quality
        if not (
            quality.numeric_consistency
            and quality.temporal_consistency
            and quality.entity_consistency
        ):
            raise ValueError("hard extraction quality gate failed")
        if self.claim_subject == "MACRO_EXPOSURE" and self.macro_variable is None:
            raise ValueError("macro_variable is required for MACRO_EXPOSURE")
        if self.claim_subject == "CAPITAL_STRUCTURE":
            if self.instrument_type is None:
                raise ValueError("instrument_type is required for CAPITAL_STRUCTURE")
            try:
                CAPITAL_STRUCTURE_INSTRUMENT_ADAPTER.validate_python(self.instrument_type)
            except ValidationError as error:
                raise ValueError("instrument_type is invalid for CAPITAL_STRUCTURE") from error
        return self

    @field_validator("macro_variable")
    @classmethod
    def macro_only_for_macro_subject(
        cls, value: MacroVariable | None, info: ValidationInfo
    ) -> MacroVariable | None:
        subject = info.data.get("claim_subject")
        if subject != "MACRO_EXPOSURE" and value is not None:
            raise ValueError("macro_variable is only valid for MACRO_EXPOSURE")
        return value


def sanitize_claim_text(text: str) -> str:
    cleaned = text
    for pattern in INJECTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return " ".join(cleaned.split())


def source_reliability(source_ref: SourceRef) -> float:
    return SOURCE_RELIABILITY.get(source_ref.content_source, source_ref.source_reliability)
