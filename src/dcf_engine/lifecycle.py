"""Lifecycle classification for valuation behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

type LifecycleStage = Literal["young", "growth", "mature", "decline"]
type ValuationMode = Literal["young_uncertain", "hybrid", "established", "distress_focus"]
type MarginTrend = Literal["expanding", "stable", "compressing"]

VALUATION_MODE_BY_STAGE: Final[dict[LifecycleStage, ValuationMode]] = {
    "young": "young_uncertain",
    "growth": "hybrid",
    "mature": "established",
    "decline": "distress_focus",
}


@dataclass(frozen=True)
class CompanySnapshot:
    revenue_cagr_3y: float
    operating_margin: float
    fcfe_recent: float
    reinvestment_rate: float
    years_since_ipo: int
    margin_trend: MarginTrend
    returns_capital: bool


def classify_lifecycle(company: CompanySnapshot) -> LifecycleStage:
    if (
        company.years_since_ipo < 3
        or company.revenue_cagr_3y > 0.40
        and company.operating_margin < 0
        and company.fcfe_recent < 0
    ):
        return "young"
    if company.revenue_cagr_3y < 0 and company.margin_trend == "compressing":
        return "decline"
    if (
        company.revenue_cagr_3y < 0.08
        and company.operating_margin > 0.10
        and company.fcfe_recent > 0
        and company.returns_capital
    ):
        return "mature"
    return "growth"


def valuation_mode_for_stage(stage: LifecycleStage) -> ValuationMode:
    return VALUATION_MODE_BY_STAGE[stage]
