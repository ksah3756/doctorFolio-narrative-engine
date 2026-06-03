"""Factor state, decay, and uncertainty calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from dcf_engine.claim import Claim, ClaimNature
from dcf_engine.lifecycle import LifecycleStage

type FactorName = Literal[
    "DemandStrength",
    "CompetitiveAdvantage",
    "OperatingEfficiency",
    "MacroCondition",
    "ExecutionQuality",
    "FinancialStrength",
]
type Regime = Literal["normal", "stress", "boom"]

BASE_FACTOR_UNCERTAINTY: Final[dict[LifecycleStage, float]] = {
    "young": 0.8,
    "growth": 0.5,
    "mature": 0.3,
    "decline": 0.5,
}
REGIME_UNC_MULT: Final[dict[Regime, float]] = {"normal": 1.0, "stress": 1.5, "boom": 1.1}
HALF_LIFE_BY_NATURE: Final[dict[ClaimNature, float]] = {
    "REALIZED": 90.0,
    "GUIDANCE": 90.0,
    "EXTERNAL": 60.0,
    "STRUCTURAL": 365.0,
    "RISK_FLAG": math.inf,
}


@dataclass(frozen=True)
class FactorState:
    name: FactorName | str
    current_value: float


def decay_weight(claim: Claim, now: datetime) -> float:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    half_life = float(HALF_LIFE_BY_NATURE[claim.claim_nature])
    if math.isinf(half_life):
        return 1.0
    published = datetime.combine(claim.published_date, datetime.min.time(), tzinfo=UTC)
    days = max((now - published).days, 0)
    return float(0.5 ** (days / half_life))


def factor_uncertainty(
    factor_name: FactorName | str,
    stage: LifecycleStage,
    regime: Regime,
    contributing_claims: list[Claim],
    now: datetime,
) -> float:
    n_eff = sum(decay_weight(claim, now) for claim in contributing_claims)
    base = BASE_FACTOR_UNCERTAINTY[stage] / math.sqrt(1 + n_eff)
    return base * REGIME_UNC_MULT[regime] * regime_factor_extra(factor_name, regime)


def regime_factor_extra(factor_name: FactorName | str, regime: Regime) -> float:
    if regime == "stress" and factor_name in ("MacroCondition", "FinancialStrength"):
        return 1.4
    return 1.0
