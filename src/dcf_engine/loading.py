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


def shifted_mu(
    assumption: AssumptionState,
    factors: Mapping[str, float],
    *,
    stage: LifecycleStage | None = None,
    company: Mapping[str, float] | None = None,
    t_year: float = 0.0,
) -> float:
    factor_loadings = LOADING.get(assumption.name, {})
    mu_shift = sum(
        factor_loadings[name] * factors[name] for name in factor_loadings if name in factors
    )
    next_mu = assumption.base_mu + mu_shift * assumption.shift_scale.center
    # 기존 2인자 호출은 loading shift만 계산하므로 company가 없으면 후처리를 생략한다.
    if company is None:
        return next_mu
    # stage별 보정은 아직 없지만 두 호출 경로가 같은 valuation context를 전달하게 한다.
    _ = stage
    reverted_mu = apply_mean_reversion(
        replace(assumption, current_mu=next_mu),
        t_year=t_year,
        company=company,
    )
    return apply_constraints(reverted_mu, assumption, company)


def apply_factor_loadings(
    assumptions: list[AssumptionState],
    factors: Mapping[str, FactorState],
    *,
    stage: LifecycleStage,
    company: Mapping[str, float],
    t_year: float,
) -> dict[str, AssumptionState]:
    shifted: dict[str, AssumptionState] = {}
    factor_values = {name: factor.current_value for name, factor in factors.items()}
    for assumption in assumptions:
        if not assumption.active:
            continue
        next_mu = shifted_mu(
            assumption,
            factor_values,
            stage=stage,
            company=company,
            t_year=t_year,
        )
        shifted[assumption.name] = replace(assumption, current_mu=next_mu)
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
        return min(max(value, risk_free), 0.30)
    if assumption.name == "OPERATING_MARGIN":
        return min(max(value, -0.5), company["industry_top_decile"] * 1.1)
    if assumption.name in ("MARKET_SHARE", "DEFAULT_PROBABILITY"):
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
