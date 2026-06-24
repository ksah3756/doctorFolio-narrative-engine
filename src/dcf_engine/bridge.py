"""Enterprise value to equity bridge."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Final, overload

import numpy as np
from numpy.typing import NDArray

from dcf_engine.capital_structure import (
    INSTRUMENT_TO_BRIDGE,
    apply_capital_structure_claims,
)

__all__ = [
    "BridgeInputs",
    "INSTRUMENT_TO_BRIDGE",
    "apply_capital_structure_claims",
    "equity_value",
    "equity_value_samples",
]

MIN_BRIDGE_VALUE: Final = 0.0
MIN_DEFAULT_PROBABILITY: Final = 0.0
MAX_DEFAULT_PROBABILITY: Final = 1.0


@dataclass(frozen=True)
class BridgeInputs:
    going_concern_firm_value: float
    liquidation_firm_value: float
    default_probability: float
    interest_bearing_debt: float
    lease_liability: float
    minority_interest: float
    cash_and_non_operating_assets: float
    option_value: float

    def __post_init__(self) -> None:
        if not isfinite(self.going_concern_firm_value):
            raise ValueError("going_concern_firm_value must be finite")
        nonnegative_values = {
            "liquidation_firm_value": self.liquidation_firm_value,
            "interest_bearing_debt": self.interest_bearing_debt,
            "lease_liability": self.lease_liability,
            "minority_interest": self.minority_interest,
            "cash_and_non_operating_assets": self.cash_and_non_operating_assets,
            "option_value": self.option_value,
        }
        for name, value in nonnegative_values.items():
            if not isfinite(value) or value < MIN_BRIDGE_VALUE:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not isfinite(self.default_probability) or not (
            MIN_DEFAULT_PROBABILITY
            <= self.default_probability
            <= MAX_DEFAULT_PROBABILITY
        ):
            raise ValueError("default_probability must be finite and between 0 and 1")


def equity_value(inputs: BridgeInputs) -> float:
    distress_adjusted = _distress_adjusted_firm_value(
        inputs.going_concern_firm_value,
        inputs.liquidation_firm_value,
        inputs.default_probability,
    )
    value = (
        distress_adjusted
        - inputs.interest_bearing_debt
        - inputs.lease_liability
        - inputs.minority_interest
        + inputs.cash_and_non_operating_assets
        - inputs.option_value
    )
    if not isfinite(value):
        raise ValueError("equity_value must be finite")
    return value


def equity_value_samples(
    base: BridgeInputs,
    default_probability_samples: NDArray[np.float64],
    *,
    going_concern_firm_value_samples: NDArray[np.float64] | None = None,
) -> NDArray[np.float64]:
    probabilities = np.asarray(default_probability_samples, dtype=np.float64)
    if not np.all(np.isfinite(probabilities)) or np.any(
        (probabilities < MIN_DEFAULT_PROBABILITY)
        | (probabilities > MAX_DEFAULT_PROBABILITY)
    ):
        raise ValueError(
            "default_probability_samples must be finite and between 0 and 1"
        )

    going_concern: float | NDArray[np.float64] = base.going_concern_firm_value
    if going_concern_firm_value_samples is not None:
        going_concern = np.asarray(
            going_concern_firm_value_samples,
            dtype=np.float64,
        )
        if going_concern.shape != probabilities.shape:
            raise ValueError(
                "going_concern_firm_value_samples must match probability sample shape"
            )
        if not np.all(np.isfinite(going_concern)):
            raise ValueError("going_concern_firm_value_samples must be finite")

    distress_adjusted = _distress_adjusted_firm_value(
        going_concern,
        base.liquidation_firm_value,
        probabilities,
    )
    values = (
        distress_adjusted
        - base.interest_bearing_debt
        - base.lease_liability
        - base.minority_interest
        + base.cash_and_non_operating_assets
        - base.option_value
    )
    if not np.all(np.isfinite(values)):
        raise ValueError("equity_value_samples must be finite")
    return values


@overload
def _distress_adjusted_firm_value(
    going_concern_firm_value: float,
    liquidation_firm_value: float,
    default_probability: float,
) -> float: ...


@overload
def _distress_adjusted_firm_value(
    going_concern_firm_value: float,
    liquidation_firm_value: float,
    default_probability: NDArray[np.float64],
) -> NDArray[np.float64]: ...


@overload
def _distress_adjusted_firm_value(
    going_concern_firm_value: NDArray[np.float64],
    liquidation_firm_value: float,
    default_probability: NDArray[np.float64],
) -> NDArray[np.float64]: ...


def _distress_adjusted_firm_value(
    going_concern_firm_value: float | NDArray[np.float64],
    liquidation_firm_value: float,
    default_probability: float | NDArray[np.float64],
) -> float | NDArray[np.float64]:
    # 부도확률 가중은 scalar와 후속 vector 경로가 공유할 단일 계산 경계다.
    return (
        (MAX_DEFAULT_PROBABILITY - default_probability) * going_concern_firm_value
        + default_probability * liquidation_firm_value
    )
