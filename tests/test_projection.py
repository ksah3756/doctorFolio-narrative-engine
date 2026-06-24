from dataclasses import replace

import numpy as np
import pytest

from dcf_engine.projection import (
    ProjectionInputs,
    going_concern_value_samples,
    project_going_concern,
)


def test_constant_assumption_path_matches_hand_calculated_fcff_and_value() -> None:
    projection = project_going_concern(
        ProjectionInputs(
            initial_revenue=100.0,
            revenue_growth=0.10,
            operating_margin=0.20,
            tax_rate=0.25,
            sales_to_capital_ratio=2.0,
            wacc=0.10,
            terminal_growth=0.02,
            forecast_years=2,
        )
    )

    np.testing.assert_allclose(projection.yearly_revenue, [110.0, 121.0])
    np.testing.assert_allclose(projection.yearly_fcff, [11.50, 12.65])
    np.testing.assert_allclose(
        projection.discounted_fcff,
        [11.50 / 1.10, 12.65 / 1.10**2],
    )
    assert projection.terminal_fcff == pytest.approx(17.303)
    assert projection.terminal_value == pytest.approx(216.2875)
    assert projection.discounted_terminal_value == pytest.approx(216.2875 / 1.10**2)
    assert projection.going_concern_firm_value == pytest.approx(199.6590909091)


@pytest.mark.parametrize(
    "field",
    [
        "initial_revenue",
        "revenue_growth",
        "operating_margin",
        "tax_rate",
        "sales_to_capital_ratio",
        "wacc",
        "terminal_growth",
    ],
)
@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_projection_rejects_non_finite_inputs(field: str, invalid: float) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_base_inputs(), **{field: invalid})


@pytest.mark.parametrize(
    ("wacc", "terminal_growth"),
    [(0.02, 0.02), (0.01, 0.02)],
)
def test_projection_rejects_wacc_not_above_terminal_growth(
    wacc: float,
    terminal_growth: float,
) -> None:
    with pytest.raises(ValueError, match="wacc must be greater than terminal_growth"):
        replace(_base_inputs(), wacc=wacc, terminal_growth=terminal_growth)


def test_going_concern_samples_are_seed_deterministic_and_preserve_shape() -> None:
    first = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=_seeded_assumption_samples(35),
        forecast_years=3,
    )
    second = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=_seeded_assumption_samples(35),
        forecast_years=3,
    )

    assert first.shape == (2, 3)
    assert first.dtype == np.float64
    assert np.all(np.isfinite(first))
    np.testing.assert_array_equal(first, second)


def test_going_concern_samples_reject_non_finite_values() -> None:
    samples = _seeded_assumption_samples(35)
    samples["WACC"][0, 0] = np.nan

    with pytest.raises(ValueError, match="WACC samples must be finite"):
        going_concern_value_samples(
            initial_revenue=100.0,
            assumption_samples=samples,
            forecast_years=3,
        )


def _base_inputs() -> ProjectionInputs:
    return ProjectionInputs(
        initial_revenue=100.0,
        revenue_growth=0.10,
        operating_margin=0.20,
        tax_rate=0.25,
        sales_to_capital_ratio=2.0,
        wacc=0.10,
        terminal_growth=0.02,
        forecast_years=2,
    )


def _seeded_assumption_samples(seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    shape = (2, 3)
    return {
        "REVENUE_CAGR": rng.normal(0.08, 0.01, shape),
        "OPERATING_MARGIN": rng.normal(0.20, 0.01, shape),
        "TAX_RATE": rng.normal(0.22, 0.005, shape),
        "SALES_TO_CAPITAL_RATIO": rng.normal(2.0, 0.05, shape),
        "WACC": rng.normal(0.10, 0.002, shape),
        "TERMINAL_GROWTH": rng.normal(0.02, 0.001, shape),
    }
