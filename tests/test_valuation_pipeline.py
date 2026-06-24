import numpy as np

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.bridge import BridgeInputs, equity_value_samples
from dcf_engine.distributions import DistributionFamily
from dcf_engine.monte_carlo import MonteCarloConfig, mc_run
from dcf_engine.projection import going_concern_value_samples


def test_monte_carlo_samples_flow_through_dcf_and_distress_bridge() -> None:
    first = _run_pipeline(seed=35)
    second = _run_pipeline(seed=35)

    assert first.shape == (64,)
    assert np.all(np.isfinite(first))
    np.testing.assert_array_equal(first, second)


def test_equity_samples_do_not_increase_with_default_probability() -> None:
    probabilities = np.linspace(0.0, 1.0, num=11, dtype=np.float64)
    assumption_samples = _constant_assumption_samples(probabilities.shape)
    going_concern = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=assumption_samples,
        forecast_years=3,
    )
    base = _bridge_inputs(liquidation_firm_value=float(going_concern[0] * 0.25))

    values = equity_value_samples(
        base,
        probabilities,
        going_concern_firm_value_samples=going_concern,
    )

    assert np.all(base.liquidation_firm_value < going_concern)
    assert np.all(np.diff(values) <= 0.0)


def test_operating_loss_samples_flow_through_bridge_with_shape_preserved() -> None:
    margins = np.array([-0.10, -0.15, -0.20], dtype=np.float64)
    assumption_samples = _constant_assumption_samples(margins.shape)
    assumption_samples["OPERATING_MARGIN"] = margins
    going_concern = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=assumption_samples,
        forecast_years=3,
    )

    values = equity_value_samples(
        _bridge_inputs(liquidation_firm_value=20.0),
        np.full(margins.shape, 0.05, dtype=np.float64),
        going_concern_firm_value_samples=going_concern,
    )

    assert np.all(going_concern < 0.0)
    assert values.shape == margins.shape
    assert np.all(np.isfinite(values))


def test_operating_losses_move_toward_liquidation_as_default_risk_rises() -> None:
    probabilities = np.linspace(0.0, 1.0, num=11, dtype=np.float64)
    assumption_samples = _constant_assumption_samples(probabilities.shape)
    assumption_samples["OPERATING_MARGIN"] = np.full(
        probabilities.shape,
        -0.10,
        dtype=np.float64,
    )
    going_concern = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=assumption_samples,
        forecast_years=3,
    )
    base = _bridge_inputs(liquidation_firm_value=20.0)

    values = equity_value_samples(
        base,
        probabilities,
        going_concern_firm_value_samples=going_concern,
    )
    liquidation_equity_value = (
        base.liquidation_firm_value
        - base.interest_bearing_debt
        - base.lease_liability
        - base.minority_interest
        + base.cash_and_non_operating_assets
        - base.option_value
    )

    assert np.all(going_concern < base.liquidation_firm_value)
    assert np.all(np.diff(values) >= 0.0)
    assert np.all(np.diff(np.abs(values - liquidation_equity_value)) <= 0.0)
    assert values[-1] == liquidation_equity_value


def _run_pipeline(*, seed: int) -> np.ndarray:
    result = mc_run(
        factor_states={},
        assumptions=_assumptions(),
        stage="growth",
        regime="normal",
        company=_company(),
        config=MonteCarloConfig(iterations=64, seed=seed),
    )
    going_concern = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=result.samples,
        forecast_years=3,
    )
    return equity_value_samples(
        _bridge_inputs(liquidation_firm_value=20.0),
        result.samples["DEFAULT_PROBABILITY"],
        going_concern_firm_value_samples=going_concern,
    )


def _constant_assumption_samples(shape: tuple[int, ...]) -> dict[str, np.ndarray]:
    values = {
        "REVENUE_CAGR": 0.08,
        "OPERATING_MARGIN": 0.20,
        "TAX_RATE": 0.22,
        "SALES_TO_CAPITAL_RATIO": 2.0,
        "WACC": 0.10,
        "TERMINAL_GROWTH": 0.02,
    }
    return {
        name: np.full(shape, value, dtype=np.float64)
        for name, value in values.items()
    }


def _assumptions() -> list[AssumptionState]:
    return [
        _assumption("REVENUE_CAGR", 0.08, 0.01, "normal"),
        _assumption("OPERATING_MARGIN", 0.20, 0.01, "normal"),
        _assumption("TAX_RATE", 0.20, 0.005, "normal"),
        _assumption("SALES_TO_CAPITAL_RATIO", 2.0, 0.05, "lognormal"),
        _assumption("WACC", 0.10, 0.002, "normal"),
        _assumption("TERMINAL_GROWTH", 0.02, 0.001, "normal"),
        _assumption("DEFAULT_PROBABILITY", 0.05, 0.01, "beta"),
    ]


def _assumption(
    name: str,
    mu: float,
    sigma: float,
    family: DistributionFamily,
) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=mu,
        current_sigma=sigma,
        base_mu=mu,
        base_sigma=sigma,
        shift_scale=ScaleSpec(center=0.05, uncertainty=0.0),
        constraints={"low": 0.0, "high": 1.0},
        active=True,
    )


def _company() -> dict[str, float]:
    return {
        "operating_margin": 0.20,
        "tax_rate": 0.20,
        "wacc_estimate": 0.10,
        "competitive_advantage_score": 0.8,
        "industry_top_decile": 0.70,
        "statutory_tax_rate": 0.21,
    }


def _bridge_inputs(*, liquidation_firm_value: float) -> BridgeInputs:
    return BridgeInputs(
        going_concern_firm_value=1.0,
        liquidation_firm_value=liquidation_firm_value,
        default_probability=0.05,
        interest_bearing_debt=10.0,
        lease_liability=2.0,
        minority_interest=1.0,
        cash_and_non_operating_assets=5.0,
        option_value=1.0,
    )
