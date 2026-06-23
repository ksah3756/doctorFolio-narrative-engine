from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dcf_engine.bridge import BridgeInputs, equity_value, equity_value_samples


@given(
    probabilities=st.lists(
        st.floats(
            min_value=0.0,
            max_value=1.0,
            allow_nan=False,
            allow_infinity=False,
        ),
        min_size=1,
        max_size=100,
    )
)
def test_distress_samples_decrease_as_default_probability_increases(
    probabilities: list[float],
) -> None:
    ordered_probabilities = np.asarray(sorted(probabilities), dtype=np.float64)

    values = equity_value_samples(_base_inputs(), ordered_probabilities)

    assert np.all(np.diff(values) <= 0.0)


def test_distress_sample_boundaries_match_scalar_equity_value() -> None:
    base = _base_inputs()
    probabilities = np.array([0.0, 1.0], dtype=np.float64)

    values = equity_value_samples(base, probabilities)

    expected = np.array(
        [
            equity_value(replace(base, default_probability=0.0)),
            equity_value(replace(base, default_probability=1.0)),
        ],
        dtype=np.float64,
    )
    np.testing.assert_array_equal(values, expected)


def test_equal_firm_values_isolate_equity_from_default_probability() -> None:
    base = replace(_base_inputs(), liquidation_firm_value=4_000.0)
    probabilities = np.array([0.0, 0.25, 0.75, 1.0], dtype=np.float64)

    values = equity_value_samples(base, probabilities)

    expected = np.full(probabilities.shape, equity_value(base), dtype=np.float64)
    np.testing.assert_array_equal(values, expected)


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf, -0.01, 1.01])
def test_distress_samples_reject_invalid_probabilities(invalid: float) -> None:
    probabilities = np.array([0.5, invalid], dtype=np.float64)

    with pytest.raises(ValueError, match="default_probability_samples"):
        equity_value_samples(_base_inputs(), probabilities)


def test_empty_distress_samples_preserve_shape() -> None:
    probabilities = np.empty((2, 0), dtype=np.float64)

    values = equity_value_samples(_base_inputs(), probabilities)

    assert values.shape == probabilities.shape
    assert values.dtype == np.float64


def test_distress_samples_are_deterministic_and_leave_base_unchanged() -> None:
    base = _base_inputs()
    original = replace(base)
    probabilities = np.array([0.1, 0.3, 0.8], dtype=np.float64)

    first = equity_value_samples(base, probabilities)
    second = equity_value_samples(base, probabilities)

    np.testing.assert_array_equal(first, second)
    assert base == original
    assert base.default_probability == 0.10


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
