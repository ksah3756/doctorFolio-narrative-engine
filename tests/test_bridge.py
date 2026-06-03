import pytest

from dcf_engine.bridge import BridgeInputs, equity_value


def test_equity_bridge_applies_default_at_firm_value_level_only() -> None:
    value = equity_value(
        BridgeInputs(
            going_concern_firm_value=4_000.0,
            liquidation_firm_value=1_000.0,
            default_probability=0.10,
            interest_bearing_debt=120.0,
            lease_liability=30.0,
            minority_interest=10.0,
            cash_and_non_operating_assets=250.0,
            option_value=40.0,
        )
    )

    assert value == pytest.approx(3_730.0)
