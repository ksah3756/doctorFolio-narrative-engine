from dataclasses import replace

import numpy as np
import pytest

from dcf_engine.lifecycle import LifecycleStage
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


def test_mature_roic_path_matches_hand_calculated_reinvestment_and_value() -> None:
    projection = project_going_concern(
        ProjectionInputs(
            initial_revenue=100.0,
            revenue_growth=0.05,
            operating_margin=0.20,
            tax_rate=0.25,
            roic=0.15,
            wacc=0.10,
            terminal_growth=0.02,
            forecast_years=2,
            stage="mature",
        )
    )

    np.testing.assert_allclose(projection.yearly_revenue, [105.0, 110.25])
    np.testing.assert_allclose(projection.yearly_reinvestment, [5.25, 5.5125])
    np.testing.assert_allclose(projection.yearly_fcff, [10.50, 11.025])
    np.testing.assert_allclose(
        projection.discounted_fcff,
        [10.50 / 1.10, 11.025 / 1.10**2],
    )
    assert projection.terminal_reinvestment == pytest.approx(2.2491)
    assert projection.terminal_fcff == pytest.approx(14.61915)
    assert projection.terminal_value == pytest.approx(182.739375)
    assert projection.discounted_terminal_value == pytest.approx(151.0242768595)
    assert projection.going_concern_firm_value == pytest.approx(169.6813016529)


@pytest.mark.parametrize(
    ("stage", "expected_fcff"),
    [
        ("young", 11.50),
        ("growth", 11.50),
        ("mature", 5.50),
        ("decline", 5.50),
    ],
)
def test_lifecycle_stage_selects_exactly_one_reinvestment_tool(
    stage: LifecycleStage,
    expected_fcff: float,
) -> None:
    projection = project_going_concern(
        replace(
            _base_inputs(),
            stage=stage,
            roic=0.15,
            forecast_years=1,
        )
    )

    assert projection.yearly_fcff[0] == pytest.approx(expected_fcff)


@pytest.mark.parametrize(
    ("stage", "field", "message"),
    [
        ("growth", "sales_to_capital_ratio", "sales_to_capital_ratio is required"),
        ("mature", "roic", "roic is required"),
    ],
)
def test_projection_requires_the_active_reinvestment_tool(
    stage: LifecycleStage,
    field: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _replace_optional_float_field(
            replace(_base_inputs(), stage=stage, roic=0.15),
            field,
            None,
        )


@pytest.mark.parametrize(
    ("stage", "field"),
    [
        ("growth", "sales_to_capital_ratio"),
        ("mature", "roic"),
    ],
)
@pytest.mark.parametrize("invalid", [0.0, -1.0, np.nan, np.inf, -np.inf])
def test_projection_rejects_invalid_active_reinvestment_tool(
    stage: LifecycleStage,
    field: str,
    invalid: float,
) -> None:
    with pytest.raises(ValueError, match=field):
        _replace_optional_float_field(
            replace(_base_inputs(), stage=stage, roic=0.15),
            field,
            invalid,
        )


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
        _replace_float_field(_base_inputs(), field, invalid)


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


@pytest.mark.parametrize(
    ("stage", "active_sample"),
    [("growth", "SALES_TO_CAPITAL_RATIO"), ("mature", "ROIC")],
)
def test_going_concern_samples_require_the_active_reinvestment_tool(
    stage: LifecycleStage,
    active_sample: str,
) -> None:
    samples = _stage_assumption_samples(stage)
    samples.pop(active_sample)

    with pytest.raises(ValueError, match=f"missing assumption samples: {active_sample}"):
        going_concern_value_samples(
            initial_revenue=100.0,
            assumption_samples=samples,
            forecast_years=3,
            stage=stage,
        )


@pytest.mark.parametrize(
    ("stage", "active_sample"),
    [("growth", "SALES_TO_CAPITAL_RATIO"), ("mature", "ROIC")],
)
@pytest.mark.parametrize("invalid", [0.0, -1.0, np.nan, np.inf, -np.inf])
def test_going_concern_samples_reject_invalid_active_reinvestment_tool(
    stage: LifecycleStage,
    active_sample: str,
    invalid: float,
) -> None:
    samples = _stage_assumption_samples(stage)
    samples[active_sample][0, 0] = invalid

    with pytest.raises(ValueError, match=active_sample):
        going_concern_value_samples(
            initial_revenue=100.0,
            assumption_samples=samples,
            forecast_years=3,
            stage=stage,
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


def _replace_float_field(
    inputs: ProjectionInputs,
    field: str,
    value: float,
) -> ProjectionInputs:
    if field == "initial_revenue":
        return replace(inputs, initial_revenue=value)
    if field == "revenue_growth":
        return replace(inputs, revenue_growth=value)
    if field == "operating_margin":
        return replace(inputs, operating_margin=value)
    if field == "tax_rate":
        return replace(inputs, tax_rate=value)
    if field == "sales_to_capital_ratio":
        return replace(inputs, sales_to_capital_ratio=value)
    if field == "wacc":
        return replace(inputs, wacc=value)
    if field == "terminal_growth":
        return replace(inputs, terminal_growth=value)
    raise AssertionError(f"unknown projection field: {field}")


def _replace_optional_float_field(
    inputs: ProjectionInputs,
    field: str,
    value: float | None,
) -> ProjectionInputs:
    if field == "sales_to_capital_ratio":
        return replace(inputs, sales_to_capital_ratio=value)
    if field == "roic":
        return replace(inputs, roic=value)
    raise AssertionError(f"unknown projection tool field: {field}")


def _stage_assumption_samples(stage: LifecycleStage) -> dict[str, np.ndarray]:
    samples = _seeded_assumption_samples(35)
    if stage == "mature":
        samples.pop("SALES_TO_CAPITAL_RATIO")
        samples["ROIC"] = np.full((2, 3), 0.15, dtype=np.float64)
    return samples


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
