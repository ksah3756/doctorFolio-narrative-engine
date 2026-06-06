"""Claim-to-factor deterministic routing."""

from __future__ import annotations

from collections import defaultdict
from typing import Final

from dcf_engine.claim import Claim, ClaimSubject, source_reliability
from dcf_engine.factor import FactorName, FactorState
from dcf_engine.lifecycle import LifecycleStage

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
    totals: defaultdict[str, float] = defaultdict(float)
    same_direction_counts: defaultdict[tuple[str, int], int] = defaultdict(int)
    for claim in claims:
        for factor_name, intensity in _routing_for_claim(claim).items():
            raw = factor_shift(claim, intensity, stage)
            sign_key = 1 if raw >= 0 else -1
            # 같은 방향 근거는 factor를 보강하되 폭주하지 않도록 점진적으로 감쇄한다.
            saturation = 1 / (1 + same_direction_counts[(factor_name, sign_key)] * 0.3)
            same_direction_counts[(factor_name, sign_key)] += 1
            totals[factor_name] += raw * saturation
    return {
        name: FactorState(name=name, current_value=max(min(value, 3.0), -3.0))
        for name, value in totals.items()
    }


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


def _routing_for_claim(claim: Claim) -> dict[FactorName, float]:
    if claim.claim_subject == "MACRO_EXPOSURE" and claim.macro_variable is not None:
        return MACRO_ROUTING.get(claim.macro_variable, {})
    return ROUTING[claim.claim_subject]
