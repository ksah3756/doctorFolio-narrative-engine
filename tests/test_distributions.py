import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dcf_engine.distributions import (
    beta_from_moments,
    lognormal_from_moments,
    lognormal_scale_from_median,
    sample_distribution,
    t_params_from_moments,
    triangular_from_mu,
)


@given(
    mean=st.floats(min_value=1.0, max_value=1_000.0, allow_nan=False, allow_infinity=False),
    cv=st.floats(min_value=0.02, max_value=0.8, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=60)
def test_lognormal_from_moments_round_trips_mean_and_std(mean: float, cv: float) -> None:
    std = mean * cv

    mu_ln, sigma_ln = lognormal_from_moments(mean, std)

    recovered_mean = math.exp(mu_ln + sigma_ln**2 / 2)
    recovered_std = math.sqrt((math.exp(sigma_ln**2) - 1) * math.exp(2 * mu_ln + sigma_ln**2))
    assert recovered_mean == pytest.approx(mean, rel=1e-6, abs=1e-9)
    assert recovered_std == pytest.approx(std, rel=1e-6, abs=1e-9)


@given(
    median=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
    std=st.floats(min_value=0.01, max_value=20.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=40)
def test_lognormal_scale_from_median_preserves_median(median: float, std: float) -> None:
    mu_ln, sigma_ln = lognormal_scale_from_median(median, std)

    assert math.exp(mu_ln) == pytest.approx(median, rel=1e-6, abs=1e-9)
    assert sigma_ln > 0


def test_beta_from_moments_returns_positive_shape_parameters() -> None:
    alpha, beta = beta_from_moments(0.6, 0.1)

    assert alpha > 0
    assert beta > 0
    assert alpha / (alpha + beta) == pytest.approx(0.6)


def test_student_t_params_match_requested_variance() -> None:
    loc, scale, df = t_params_from_moments(0.22, 0.08)

    assert loc == pytest.approx(0.22)
    assert scale * math.sqrt(df / (df - 2)) == pytest.approx(0.08)


def test_triangular_mode_is_clipped_inside_bounds() -> None:
    assert triangular_from_mu(0.08, 0.0, 0.04) == (0.0, 0.04, 0.04)


def test_sampling_is_seed_deterministic() -> None:
    rng_one = np.random.default_rng(42)
    rng_two = np.random.default_rng(42)

    assert sample_distribution("normal", (1.0, 0.2), rng_one) == pytest.approx(
        sample_distribution("normal", (1.0, 0.2), rng_two)
    )
