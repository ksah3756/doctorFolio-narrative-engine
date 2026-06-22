"""Claim-to-factor deterministic routing."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Final, Literal

from dcf_engine.claim import Claim, ClaimDirection, ClaimSubject, source_reliability
from dcf_engine.factor import FactorName, FactorState
from dcf_engine.lifecycle import LifecycleStage

type EconomicDriverName = Literal[
    "capital_return",
    "china_export_risk",
    "customer_concentration",
    "financial_performance",
    "gross_margin",
    "non_recurring_financial",
    "opex_pressure",
    "revenue_acceleration",
    "tariff_pressure",
    "subject_signal",
]


@dataclass(frozen=True)
class EconomicDriver:
    name: EconomicDriverName
    direction: ClaimDirection
    claim: Claim


MAGNITUDE_TO_SIGMA: Final[dict[str, float]] = {
    "WEAK": 0.25,
    "MODERATE": 0.5,
    "STRONG": 1.0,
    "EXTREME": 1.5,
}
NATURE_INFO_WEIGHT: Final[dict[str, float]] = {
    "REALIZED": 1.0,
    "GUIDANCE": 0.7,
    "EXTERNAL": 0.6,
    "STRUCTURAL": 1.2,
    "RISK_FLAG": 0.8,
}
OPEX_PRESSURE_WITH_MARGIN_RECOVERY_MULT: Final = 0.25
NARRATIVE_SENSITIVITY_BY_STAGE: Final[dict[LifecycleStage, float]] = {
    "young": 1.5,
    "growth": 1.2,
    "mature": 0.8,
    "decline": 1.0,
}
DECLINE_SUBJECT_SENSITIVITY: Final[dict[ClaimSubject, float]] = {
    "FINANCIAL_HEALTH": 1.5,
    "CAPITAL_STRUCTURE": 1.3,
    "GOVERNANCE": 1.2,
    "DEMAND_SIGNAL": 0.5,
    "MARKET_STRUCTURE": 0.4,
}
ROUTING: Final[dict[ClaimSubject, dict[FactorName, float]]] = {
    "DEMAND_SIGNAL": {"DemandStrength": 1.0, "CompetitiveAdvantage": 0.2},
    "SUPPLY_SIGNAL": {"DemandStrength": 0.4, "OperatingEfficiency": 0.4},
    "PRICING_SIGNAL": {"CompetitiveAdvantage": 0.6, "OperatingEfficiency": 0.3},
    "COST_SIGNAL": {"OperatingEfficiency": -0.7, "MacroCondition": -0.2},
    "CAPITAL_ALLOCATION": {"DemandStrength": 0.3},
    "COMPETITIVE_POSITION": {"CompetitiveAdvantage": 1.0},
    "MARKET_STRUCTURE": {"DemandStrength": 0.6, "CompetitiveAdvantage": 0.2},
    "FINANCIAL_HEALTH": {"FinancialStrength": 1.0},
    "GOVERNANCE": {"ExecutionQuality": 0.7, "FinancialStrength": 0.2},
    "MACRO_EXPOSURE": {},
    "CAPITAL_STRUCTURE": {},
}
MACRO_ROUTING: Final[dict[str, dict[FactorName, float]]] = {
    "RATE": {"MacroCondition": -1.0},
    "INFLATION": {"MacroCondition": -0.6, "OperatingEfficiency": -0.3},
    "COMMODITY": {"OperatingEfficiency": -0.4},
}


def route_claims_to_factors(claims: list[Claim], stage: LifecycleStage) -> dict[str, FactorState]:
    drivers = claims_to_economic_drivers(claims)
    has_margin_recovery = any(
        driver.name == "gross_margin" and driver.direction == "INCREASE" for driver in drivers
    )
    totals: defaultdict[str, float] = defaultdict(float)
    same_direction_counts: defaultdict[tuple[str, int], int] = defaultdict(int)
    for driver in drivers:
        for factor_name, intensity in _routing_for_driver(
            driver, has_margin_recovery=has_margin_recovery
        ).items():
            raw = factor_shift(driver.claim, intensity, stage)
            sign_key = 1 if raw >= 0 else -1
            # 같은 방향 근거는 factor를 보강하되 폭주하지 않도록 점진적으로 감쇄한다.
            saturation = 1 / (1 + same_direction_counts[(factor_name, sign_key)] * 0.3)
            same_direction_counts[(factor_name, sign_key)] += 1
            totals[factor_name] += raw * saturation
    return {
        name: FactorState(name=name, current_value=max(min(value, 3.0), -3.0))
        for name, value in totals.items()
    }


def claims_to_economic_drivers(claims: list[Claim]) -> list[EconomicDriver]:
    selected: dict[tuple[EconomicDriverName, ClaimDirection, str], Claim] = {}
    for claim in claims:
        driver_name = economic_driver_name(claim)
        key = (driver_name, claim.direction, _driver_scope(driver_name, claim))
        current = selected.get(key)
        if current is None or _driver_evidence_weight(claim) > _driver_evidence_weight(current):
            selected[key] = claim
    return [
        EconomicDriver(name=name, direction=direction, claim=claim)
        for (name, direction, _), claim in selected.items()
    ]


def factor_shift(claim: Claim, routing_intensity: float, stage: LifecycleStage) -> float:
    # claim은 사실 방향만 담고, valuation 부호와 중요도는 deterministic routing이 책임진다.
    dir_sign = {"INCREASE": 1.0, "DECREASE": -1.0, "NEUTRAL": 0.0}[claim.direction]
    value = (
        MAGNITUDE_TO_SIGMA[claim.magnitude_qualifier]
        * NATURE_INFO_WEIGHT[claim.claim_nature]
        * source_reliability(claim.source_ref)
        * routing_intensity
        * narrative_sensitivity(stage, claim.claim_subject)
    )
    return max(min(dir_sign * value, 1.5), -1.5)


def narrative_sensitivity(stage: LifecycleStage, subject: ClaimSubject) -> float:
    if stage == "decline":
        return DECLINE_SUBJECT_SENSITIVITY.get(subject, 0.6)
    return NARRATIVE_SENSITIVITY_BY_STAGE[stage]


def economic_driver_name(claim: Claim) -> EconomicDriverName:
    text = claim.claim_text.lower()
    if claim.claim_subject == "CAPITAL_ALLOCATION" or _mentions(text, "repurchase", "dividend"):
        return "capital_return"
    if _mentions(
        text,
        "interest income",
        "other income",
        "unrealized gains",
        "equity securities",
        "non-marketable",
    ):
        return "non_recurring_financial"
    if _mentions(text, "tariff"):
        return "tariff_pressure"
    if _mentions(text, "china", "export control", "h200", "foreclosed"):
        return "china_export_risk"
    if _mentions(text, "customer") and _mentions(text, "represented", "concentrat"):
        return "customer_concentration"
    if _mentions(text, "gross margin", "operating margin"):
        return "gross_margin"
    if claim.claim_subject == "COST_SIGNAL" and _mentions(
        text,
        "operating expenses",
        "research and development",
        "sales, general and administrative",
        "compensation",
        "compute and infrastructure",
        "engineering development",
    ):
        return "opex_pressure"
    if claim.claim_subject == "DEMAND_SIGNAL" and _mentions(text, "revenue", "sales"):
        return "revenue_acceleration"
    if claim.claim_subject == "FINANCIAL_HEALTH":
        return "financial_performance"
    return "subject_signal"


def _routing_for_driver(
    driver: EconomicDriver, *, has_margin_recovery: bool
) -> dict[FactorName, float]:
    if driver.name in ("capital_return", "customer_concentration", "non_recurring_financial"):
        return {}
    if driver.name == "china_export_risk":
        return {"DemandStrength": 0.5, "CompetitiveAdvantage": 0.5}
    routing = _routing_for_claim(driver.claim)
    if driver.name == "opex_pressure" and has_margin_recovery:
        # 매출/마진이 같이 개선되는 분기에는 절대 비용 증가를 효율 악화로 과대반영하지 않는다.
        return {
            factor_name: intensity * OPEX_PRESSURE_WITH_MARGIN_RECOVERY_MULT
            for factor_name, intensity in routing.items()
        }
    return routing


def _routing_for_claim(claim: Claim) -> dict[FactorName, float]:
    if claim.claim_subject == "MACRO_EXPOSURE" and claim.macro_variable is not None:
        return MACRO_ROUTING.get(claim.macro_variable, {})
    return ROUTING[claim.claim_subject]


def _driver_evidence_weight(claim: Claim) -> float:
    return (
        MAGNITUDE_TO_SIGMA[claim.magnitude_qualifier]
        * NATURE_INFO_WEIGHT[claim.claim_nature]
        * source_reliability(claim.source_ref)
    )


def _driver_scope(driver_name: EconomicDriverName, claim: Claim) -> str:
    if driver_name == "subject_signal":
        return claim.claim_subject
    return ""


def _mentions(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)
