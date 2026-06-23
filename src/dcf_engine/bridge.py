"""Enterprise value to equity bridge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Final, Literal, cast

from dcf_engine.claim import (
    CapitalStructureInstrument,
    Claim,
    ClaimDirection,
    MagnitudeQualifier,
)

type BridgeComponent = Literal[
    "interest_bearing_debt",
    "lease_liability",
    "option_value",
    "minority_interest",
]

INSTRUMENT_TO_BRIDGE: Final[dict[CapitalStructureInstrument, BridgeComponent]] = {
    "corporate_bond": "interest_bearing_debt",
    "bank_loan": "interest_bearing_debt",
    "lease": "lease_liability",
    "stock_option": "option_value",
    "minority_stake": "minority_interest",
}
SHARE_COUNT_INSTRUMENTS: Final[frozenset[CapitalStructureInstrument]] = frozenset(
    {"equity_issuance", "treasury_stock"}
)
MAGNITUDE_TO_BRIDGE_PERCENT: Final[dict[MagnitudeQualifier, float]] = {
    "WEAK": 0.05,
    "MODERATE": 0.10,
    "STRONG": 0.20,
    "EXTREME": 0.40,
}
DIRECTION_TO_BRIDGE_SIGN: Final[dict[ClaimDirection, float]] = {
    "INCREASE": 1.0,
    "DECREASE": -1.0,
    "NEUTRAL": 0.0,
}
MIN_BRIDGE_VALUE: Final = 0.0
MIN_DEFAULT_PROBABILITY: Final = 0.0
MAX_DEFAULT_PROBABILITY: Final = 1.0
UNCHANGED_COMPONENT_MULTIPLIER: Final = 1.0


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
        nonnegative_values = {
            "going_concern_firm_value": self.going_concern_firm_value,
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


def apply_capital_structure_claims(base: BridgeInputs, claims: list[Claim]) -> BridgeInputs:
    component_percent_changes: dict[BridgeComponent, float] = {
        "interest_bearing_debt": 0.0,
        "lease_liability": 0.0,
        "option_value": 0.0,
        "minority_interest": 0.0,
    }
    for claim in claims:
        if claim.claim_subject != "CAPITAL_STRUCTURE":
            continue
        instrument = cast(CapitalStructureInstrument, claim.instrument_type)
        if instrument in SHARE_COUNT_INSTRUMENTS:
            # 주식 수 instrument는 후속 per-share 단계 책임이므로 bridge value를 건드리지 않는다.
            continue
        component = INSTRUMENT_TO_BRIDGE[instrument]
        component_percent_changes[component] += (
            DIRECTION_TO_BRIDGE_SIGN[claim.direction]
            * MAGNITUDE_TO_BRIDGE_PERCENT[claim.magnitude_qualifier]
        )

    # 원본 component 기준 변화율을 합산해 claim 순서와 무관하게 결정론적으로 갱신한다.
    return replace(
        base,
        interest_bearing_debt=_apply_percent_change(
            base.interest_bearing_debt,
            component_percent_changes["interest_bearing_debt"],
        ),
        lease_liability=_apply_percent_change(
            base.lease_liability,
            component_percent_changes["lease_liability"],
        ),
        option_value=_apply_percent_change(
            base.option_value,
            component_percent_changes["option_value"],
        ),
        minority_interest=_apply_percent_change(
            base.minority_interest,
            component_percent_changes["minority_interest"],
        ),
    )


def _apply_percent_change(value: float, percent_change: float) -> float:
    return max(
        value * (UNCHANGED_COMPONENT_MULTIPLIER + percent_change),
        MIN_BRIDGE_VALUE,
    )


def equity_value(inputs: BridgeInputs) -> float:
    distress_adjusted = (
        (1 - inputs.default_probability) * inputs.going_concern_firm_value
        + inputs.default_probability * inputs.liquidation_firm_value
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
