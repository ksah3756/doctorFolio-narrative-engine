import math
import struct
from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.factor import FactorState
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.loading import (
    LOADING,
    apply_constraints,
    apply_factor_loadings,
    apply_mean_reversion,
)
from dcf_engine.monte_carlo import _shifted_mu

FACTOR_SIGMA_CAP = 1.5
FACTOR_NAMES: tuple[str, ...] = (
    "DemandStrength",
    "CompetitiveAdvantage",
    "OperatingEfficiency",
    "MacroCondition",
    "ExecutionQuality",
    "FinancialStrength",
)
COMPANY: dict[str, float] = {
    "operating_margin": 0.32,
    "tax_rate": 0.21,
    "wacc_estimate": 0.11,
    "competitive_advantage_score": 0.75,
    "industry_top_decile": 0.52,
    "statutory_tax_rate": 0.24,
}


@pytest.mark.parametrize(
    ("stage", "name", "base_mu", "sigma"),
    [
        ("young", "OPERATING_MARGIN", 0.38, 0.08),
        ("growth", "SALES_TO_CAPITAL_RATIO", 2.40, 0.30),
        ("mature", "ROIC", 0.19, 0.05),
        ("decline", "WACC", 0.13, 0.02),
    ],
)
def test_loading_and_monte_carlo_mu_paths_are_equivalent(
    stage: LifecycleStage,
    name: str,
    base_mu: float,
    sigma: float,
) -> None:
    assumption = _assumption(name, base_mu, sigma, shift_scale=0.05)
    sampled_factors = {
        "DemandStrength": 0.8,
        "CompetitiveAdvantage": -0.4,
        "OperatingEfficiency": 0.6,
        "MacroCondition": -0.3,
        "ExecutionQuality": 0.5,
        "FinancialStrength": -0.2,
    }

    loading_mu = _loading_mu(assumption, sampled_factors, stage=stage, t_year=4.0)
    monte_carlo_mu = _monte_carlo_mu(assumption, sampled_factors, t_year=4.0)

    assert struct.pack("!d", monte_carlo_mu) == struct.pack("!d", loading_mu)


@given(
    stage=st.sampled_from(("young", "growth", "mature", "decline")),
    name=st.sampled_from(("OPERATING_MARGIN", "ROIC", "SALES_TO_CAPITAL_RATIO", "WACC")),
    base_mu=st.floats(min_value=-0.25, max_value=0.75, allow_nan=False, allow_infinity=False),
    sigma=st.floats(min_value=0.001, max_value=0.50, allow_nan=False, allow_infinity=False),
    shift_scale=st.floats(min_value=0.001, max_value=0.25, allow_nan=False, allow_infinity=False),
    factor_values=st.dictionaries(
        keys=st.sampled_from(FACTOR_NAMES),
        values=st.floats(
            min_value=-FACTOR_SIGMA_CAP,
            max_value=FACTOR_SIGMA_CAP,
            allow_nan=False,
            allow_infinity=False,
        ),
        max_size=len(FACTOR_NAMES),
    ),
    t_year=st.floats(min_value=0.0, max_value=30.0, allow_nan=False, allow_infinity=False),
)
def test_loading_and_monte_carlo_mu_paths_agree_within_factor_cap(
    stage: LifecycleStage,
    name: str,
    base_mu: float,
    sigma: float,
    shift_scale: float,
    factor_values: dict[str, float],
    t_year: float,
) -> None:
    assumption = _assumption(name, base_mu, sigma, shift_scale=shift_scale)

    # factor 값은 routing 경계의 ±1.5σ 안에서만 생성해 실제 입력 계약을 고정한다.
    assert all(abs(value) <= FACTOR_SIGMA_CAP for value in factor_values.values())
    raw_mu = _shifted_mu(assumption, factor_values)
    loading_mu = _loading_mu(assumption, factor_values, stage=stage, t_year=t_year)
    monte_carlo_mu = _monte_carlo_mu(assumption, factor_values, t_year=t_year)

    max_raw_shift = (
        sum(
            abs(loading)
            for factor_name, loading in LOADING.get(name, {}).items()
            if factor_name in factor_values
        )
        * FACTOR_SIGMA_CAP
        * shift_scale
    )
    assert abs(raw_mu - base_mu) <= max_raw_shift + 1e-15
    assert struct.pack("!d", monte_carlo_mu) == struct.pack("!d", loading_mu)
    assert math.isfinite(loading_mu)
    assert math.isfinite(monte_carlo_mu)


def _loading_mu(
    assumption: AssumptionState,
    factor_values: Mapping[str, float],
    *,
    stage: LifecycleStage,
    t_year: float,
) -> float:
    factors = {
        name: FactorState(name=name, current_value=value)
        for name, value in factor_values.items()
    }
    shifted = apply_factor_loadings(
        [assumption],
        factors,
        stage=stage,
        company=COMPANY,
        t_year=t_year,
    )
    return shifted[assumption.name].current_mu


def _monte_carlo_mu(
    assumption: AssumptionState,
    factor_values: Mapping[str, float],
    *,
    t_year: float,
) -> float:
    shifted_mu = _shifted_mu(assumption, factor_values)
    reverted_mu = apply_mean_reversion(
        assumption.with_mu(shifted_mu),
        t_year=t_year,
        company=COMPANY,
    )
    return apply_constraints(reverted_mu, assumption, COMPANY)


def _assumption(
    name: str,
    base_mu: float,
    sigma: float,
    *,
    shift_scale: float,
) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family="normal",
        current_mu=base_mu,
        current_sigma=sigma,
        base_mu=base_mu,
        base_sigma=sigma,
        shift_scale=ScaleSpec(center=shift_scale, uncertainty=0.0),
        constraints={},
        active=True,
    )
