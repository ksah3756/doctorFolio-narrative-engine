"""Capital-structure claim mapping for the EV-to-equity bridge."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final, Literal, cast

from dcf_engine.claim import (
    CapitalStructureInstrument,
    Claim,
    ClaimDirection,
    MagnitudeQualifier,
)

if TYPE_CHECKING:
    from dcf_engine.bridge import BridgeInputs

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
UNCHANGED_COMPONENT_MULTIPLIER: Final = 1.0


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
