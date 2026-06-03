"""Enterprise value to equity bridge."""

from __future__ import annotations

from dataclasses import dataclass


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


def equity_value(inputs: BridgeInputs) -> float:
    distress_adjusted = (
        (1 - inputs.default_probability) * inputs.going_concern_firm_value
        + inputs.default_probability * inputs.liquidation_firm_value
    )
    return (
        distress_adjusted
        - inputs.interest_bearing_debt
        - inputs.lease_liability
        - inputs.minority_interest
        + inputs.cash_and_non_operating_assets
        - inputs.option_value
    )
