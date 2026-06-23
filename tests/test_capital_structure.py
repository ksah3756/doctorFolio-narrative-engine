from collections.abc import Callable
from dataclasses import replace
from datetime import date
import math

import pytest
from pydantic import ValidationError

from dcf_engine.bridge import (
    INSTRUMENT_TO_BRIDGE,
    BridgeInputs,
    apply_capital_structure_claims,
    equity_value,
)
from dcf_engine.claim import (
    CapitalStructureInstrument,
    Claim,
    ClaimDirection,
    ExtractionQuality,
    MagnitudeQualifier,
    SourceRef,
)
from dcf_engine.routing import route_claims_to_factors

type ComponentReader = Callable[[BridgeInputs], float]


def test_instrument_mapping_contains_only_direct_bridge_components() -> None:
    assert INSTRUMENT_TO_BRIDGE == {
        "corporate_bond": "interest_bearing_debt",
        "bank_loan": "interest_bearing_debt",
        "lease": "lease_liability",
        "stock_option": "option_value",
        "minority_stake": "minority_interest",
    }


@pytest.mark.parametrize(
    ("instrument", "component"),
    [
        ("corporate_bond", lambda inputs: inputs.interest_bearing_debt),
        ("bank_loan", lambda inputs: inputs.interest_bearing_debt),
        ("lease", lambda inputs: inputs.lease_liability),
        ("stock_option", lambda inputs: inputs.option_value),
        ("minority_stake", lambda inputs: inputs.minority_interest),
    ],
)
def test_each_instrument_updates_its_bridge_component(
    instrument: CapitalStructureInstrument,
    component: ComponentReader,
) -> None:
    base = _base_inputs()

    updated = apply_capital_structure_claims(base, [_claim(instrument)])

    assert component(updated) > component(base)


def test_debt_direction_has_opposite_effect_on_equity_value() -> None:
    base = _base_inputs()

    increased = apply_capital_structure_claims(
        base, [_claim("corporate_bond", direction="INCREASE")]
    )
    decreased = apply_capital_structure_claims(
        base, [_claim("corporate_bond", direction="DECREASE")]
    )

    assert increased.interest_bearing_debt > base.interest_bearing_debt
    assert equity_value(increased) < equity_value(base)
    assert decreased.interest_bearing_debt < base.interest_bearing_debt
    assert equity_value(decreased) > equity_value(base)


def test_magnitude_percentage_changes_are_strictly_monotonic() -> None:
    base = _base_inputs()
    magnitudes: tuple[MagnitudeQualifier, ...] = (
        "WEAK",
        "MODERATE",
        "STRONG",
        "EXTREME",
    )

    deltas = [
        apply_capital_structure_claims(
            base, [_claim("corporate_bond", magnitude=magnitude)]
        ).interest_bearing_debt
        - base.interest_bearing_debt
        for magnitude in magnitudes
    ]

    assert deltas == sorted(deltas)
    assert len(set(deltas)) == len(magnitudes)


def test_capital_structure_bypasses_factors_without_affecting_other_subjects() -> None:
    capital_structure = _claim("corporate_bond")
    demand = _claim(None, subject="DEMAND_SIGNAL")

    assert route_claims_to_factors([capital_structure], "growth") == {}
    assert route_claims_to_factors(
        [capital_structure, demand], "growth"
    ) == route_claims_to_factors([demand], "growth")
    assert apply_capital_structure_claims(
        _base_inputs(), [capital_structure, demand]
    ) == apply_capital_structure_claims(_base_inputs(), [capital_structure])


def test_capital_structure_rejects_unknown_instrument() -> None:
    with pytest.raises(ValidationError, match="instrument_type"):
        _claim("convertible_magic_note")


def test_capital_structure_requires_instrument() -> None:
    with pytest.raises(ValidationError, match="instrument_type"):
        _claim(None)


@pytest.mark.parametrize("invalid", [-1.0, math.nan, math.inf, -math.inf])
def test_bridge_inputs_reject_invalid_component_values(invalid: float) -> None:
    with pytest.raises(ValueError, match="interest_bearing_debt"):
        replace(_base_inputs(), interest_bearing_debt=invalid)


@pytest.mark.parametrize("invalid", [-0.01, 1.01, math.nan, math.inf])
def test_bridge_inputs_reject_invalid_default_probability(invalid: float) -> None:
    with pytest.raises(ValueError, match="default_probability"):
        replace(_base_inputs(), default_probability=invalid)


def test_repeated_decreases_clamp_bridge_component_at_zero() -> None:
    base = _base_inputs()
    decreases = [
        _claim("corporate_bond", direction="DECREASE", magnitude="EXTREME")
        for _ in range(3)
    ]

    updated = apply_capital_structure_claims(base, decreases)

    assert updated.interest_bearing_debt == 0.0
    assert math.isfinite(equity_value(updated))


@pytest.mark.parametrize("instrument", ["equity_issuance", "treasury_stock"])
def test_share_count_instruments_leave_bridge_value_unchanged(
    instrument: CapitalStructureInstrument,
) -> None:
    base = _base_inputs()

    updated = apply_capital_structure_claims(base, [_claim(instrument)])

    assert updated == base
    assert equity_value(updated) == equity_value(base)


def _base_inputs() -> BridgeInputs:
    return BridgeInputs(
        going_concern_firm_value=4_000.0,
        liquidation_firm_value=1_000.0,
        default_probability=0.10,
        interest_bearing_debt=120.0,
        lease_liability=30.0,
        minority_interest=10.0,
        cash_and_non_operating_assets=250.0,
        option_value=40.0,
    )


def _claim(
    instrument: str | None,
    *,
    subject: str = "CAPITAL_STRUCTURE",
    direction: ClaimDirection = "INCREASE",
    magnitude: MagnitudeQualifier = "STRONG",
) -> Claim:
    return Claim.model_validate(
        {
            "claim_id": f"{subject}-{instrument}-{direction}-{magnitude}",
            "claim_text": "Capital structure changed.",
            "claim_subject": subject,
            "claim_nature": "REALIZED",
            "direction": direction,
            "magnitude_qualifier": magnitude,
            "instrument_type": instrument,
            "extraction_quality": ExtractionQuality(
                verbatim_overlap=0.95,
                numeric_consistency=True,
                temporal_consistency=True,
                entity_consistency=True,
            ),
            "source_ref": SourceRef(
                discovery_channel="direct",
                content_source="10-Q",
                source_reliability=0.95,
            ),
            "chunk_ref": "capital-structure-test",
            "published_date": date(2026, 6, 23),
        }
    )
