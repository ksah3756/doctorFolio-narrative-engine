"""Assumption state and lifecycle-specific reinvestment rules."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal, overload

import numpy as np
from numpy.typing import NDArray

from dcf_engine.distributions import DistributionFamily, lognormal_scale_from_three_points
from dcf_engine.lifecycle import LifecycleStage

type AssumptionName = Literal[
    "TAM",
    "MARKET_SHARE",
    "REVENUE_CAGR",
    "TERMINAL_GROWTH",
    "OPERATING_MARGIN",
    "TAX_RATE",
    "SALES_TO_CAPITAL_RATIO",
    "ROIC",
    "WACC",
    "DEFAULT_PROBABILITY",
]
type ReinvestmentTool = Literal["sales_to_capital", "roic"]

REINVESTMENT_TOOL_BY_STAGE: Final[dict[LifecycleStage, ReinvestmentTool]] = {
    "young": "sales_to_capital",
    "growth": "sales_to_capital",
    "mature": "roic",
    "decline": "roic",
}


@dataclass(frozen=True)
class ScaleSpec:
    center: float
    uncertainty: float

    @classmethod
    def from_three_points(cls, low: float, base: float, high: float) -> ScaleSpec:
        _, sigma_ln = lognormal_scale_from_three_points(low, base, high)
        uncertainty = base * math.sqrt(math.expm1(sigma_ln**2))
        return cls(center=base, uncertainty=uncertainty)


@dataclass(frozen=True)
class AssumptionState:
    name: str
    distribution_family: DistributionFamily
    current_mu: float
    current_sigma: float
    base_mu: float
    base_sigma: float
    shift_scale: ScaleSpec
    constraints: Mapping[str, float]
    active: bool

    def with_mu(self, current_mu: float) -> AssumptionState:
        return replace(self, current_mu=current_mu)


@overload
def compute_reinvestment(
    stage: LifecycleStage,
    *,
    delta_revenue: float,
    nopat: float,
    growth: float,
    tool_value: float,
) -> float: ...


@overload
def compute_reinvestment(
    stage: LifecycleStage,
    *,
    delta_revenue: NDArray[np.float64],
    nopat: NDArray[np.float64],
    growth: NDArray[np.float64],
    tool_value: NDArray[np.float64],
) -> NDArray[np.float64]: ...


def compute_reinvestment(
    stage: LifecycleStage,
    *,
    delta_revenue: float | NDArray[np.float64],
    nopat: float | NDArray[np.float64],
    growth: float | NDArray[np.float64],
    tool_value: float | NDArray[np.float64],
) -> float | NDArray[np.float64]:
    if np.any(tool_value <= 0):
        raise ValueError("tool_value must be positive")
    if REINVESTMENT_TOOL_BY_STAGE[stage] == "sales_to_capital":
        return delta_revenue / tool_value
    return nopat * (growth / tool_value)
