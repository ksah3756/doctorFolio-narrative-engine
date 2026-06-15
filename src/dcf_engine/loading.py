"""Factor-to-assumption loading and mean reversion."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Final

from dcf_engine.assumption import AssumptionState
from dcf_engine.factor import FactorState
from dcf_engine.lifecycle import LifecycleStage

SECTOR_MEDIAN: Final[dict[str, float]] = {
    "REVENUE_CAGR": 0.10,
    "OPERATING_MARGIN": 0.28,
    "SALES_TO_CAPITAL_RATIO": 2.8,
    "ROIC": 0.16,
}
NARRATIVE_DEFAULT_PROBABILITY_CAP: Final = 0.05
NARRATIVE_WACC_BAND: Final = 0.015
MEAN_REVERT_TARGETS: Final[set[str]] = {
    "OPERATING_MARGIN",
    "SALES_TO_CAPITAL_RATIO",
    "ROIC",
    "REVENUE_CAGR",
}
LOADING: Final[dict[str, dict[str, float]]] = {
    "TAM": {"DemandStrength": 0.6, "CompetitiveAdvantage": 0.2, "MacroCondition": 0.2},
    "MARKET_SHARE": {
        "DemandStrength": 0.1,
        "CompetitiveAdvantage": 0.8,
        "OperatingEfficiency": 0.1,
        "ExecutionQuality": 0.1,
    },
    "REVENUE_CAGR": {
        "DemandStrength": 0.7,
        "CompetitiveAdvantage": 0.4,
        "MacroCondition": 0.2,
        "ExecutionQuality": 0.2,
        "FinancialStrength": 0.1,
    },
    "OPERATING_MARGIN": {
        "DemandStrength": 0.2,
        "CompetitiveAdvantage": 0.5,
        "OperatingEfficiency": 0.6,
        "MacroCondition": 0.1,
        "ExecutionQuality": 0.3,
        "FinancialStrength": 0.1,
    },
    "SALES_TO_CAPITAL_RATIO": {"OperatingEfficiency": 0.5, "ExecutionQuality": 0.4},
    "WACC": {"OperatingEfficiency": -0.1, "MacroCondition": -0.7, "FinancialStrength": -0.2},
    "DEFAULT_PROBABILITY": {
        "OperatingEfficiency": -0.1,
        "MacroCondition": -0.2,
        "ExecutionQuality": -0.2,
        "FinancialStrength": -0.9,
    },
}


def apply_factor_loadings(
    assumptions: list[AssumptionState],
    factors: Mapping[str, FactorState],
    *,
    stage: LifecycleStage,
    company: Mapping[str, float],
    t_year: float,
) -> dict[str, AssumptionState]:
    shifted: dict[str, AssumptionState] = {}
    for assumption in assumptions:
        if not assumption.active:
            continue
        mu_shift = sum(
            loading * factors[name].current_value
            for name, loading in LOADING.get(assumption.name, {}).items()
            if name in factors
        )
        scale = assumption.shift_scale.center
        next_mu = assumption.base_mu + mu_shift * scale
        next_mu = apply_mean_reversion(
            replace(assumption, current_mu=next_mu), t_year=t_year, company=company
        )
        constrained_mu = apply_constraints(next_mu, assumption, company)
        shifted[assumption.name] = replace(assumption, current_mu=constrained_mu)
    return shifted


def apply_mean_reversion(
    assumption: AssumptionState, *, t_year: float, company: Mapping[str, float]
) -> float:
    if assumption.name not in MEAN_REVERT_TARGETS:
        return assumption.current_mu
    target = reversion_target(assumption, company)
    tau = reversion_speed(company)
    return assumption.current_mu + (target - assumption.current_mu) * (1 - math.exp(-t_year / tau))


def reversion_target(assumption: AssumptionState, company: Mapping[str, float]) -> float:
    base = SECTOR_MEDIAN[assumption.name]
    if assumption.name in ("ROIC", "SALES_TO_CAPITAL_RATIO"):
        return max(base, roic_equals_wacc_level(assumption, company))
    return base


def roic_equals_wacc_level(assumption: AssumptionState, company: Mapping[str, float]) -> float:
    if assumption.name == "ROIC":
        return company["wacc_estimate"]
    after_tax_margin = company["operating_margin"] * (1 - company["tax_rate"])
    if after_tax_margin <= 0:
        return SECTOR_MEDIAN["SALES_TO_CAPITAL_RATIO"]
    return company["wacc_estimate"] / after_tax_margin


def reversion_speed(company: Mapping[str, float]) -> float:
    return 3 + company["competitive_advantage_score"] * 12


def apply_constraints(
    value: float, assumption: AssumptionState, company: Mapping[str, float]
) -> float:
    if assumption.name == "TERMINAL_GROWTH":
        return min(value, assumption.constraints.get("risk_free_rate", 0.045))
    if assumption.name == "WACC":
        risk_free = assumption.constraints.get("risk_free_rate", 0.045)
        band = company.get("narrative_wacc_band", NARRATIVE_WACC_BAND)
        low = max(risk_free, assumption.base_mu - band)
        high = min(assumption.constraints.get("high", 0.30), assumption.base_mu + band)
        # WACC는 narrative로 방향성만 조정한다.
        # 할인율 체계 자체는 별도 credit/capital model에 맡긴다.
        return min(max(value, low), high)
    if assumption.name == "OPERATING_MARGIN":
        return min(max(value, -0.5), company["industry_top_decile"] * 1.1)
    if assumption.name in ("MARKET_SHARE", "DEFAULT_PROBABILITY"):
        if assumption.name == "DEFAULT_PROBABILITY":
            high = min(
                assumption.constraints.get("high", 1 - 1e-6),
                company.get(
                    "narrative_default_probability_cap",
                    NARRATIVE_DEFAULT_PROBABILITY_CAP,
                ),
            )
            # 부도확률은 재무제표 기반 base가 주도하고 narrative는 작은 premium 안에서만 움직인다.
            return min(max(value, 1e-6), high)
        return min(max(value, 1e-6), 1 - 1e-6)
    if assumption.name == "REVENUE_CAGR":
        return min(max(value, -0.5), 2.0)
    if assumption.name == "TAX_RATE":
        return min(max(value, 0.0), company["statutory_tax_rate"] * 1.2)
    if assumption.name == "SALES_TO_CAPITAL_RATIO":
        return max(value, 0.05)
    if assumption.name == "ROIC":
        return min(max(value, -0.5), 1.0)
    return value
