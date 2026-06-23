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
    NARRATIVE_SHIFT_CAPS,
    apply_constraints,
    apply_factor_loadings,
    apply_mean_reversion,
    narrative_shift_for_assumption,
    shifted_mu_from_factors,
)

STAGES: tuple[LifecycleStage, ...] = ("young", "growth", "mature", "decline")
FACTOR_NAMES: tuple[str, ...] = tuple(
    sorted({factor_name for loadings in LOADING.values() for factor_name in loadings})
)


def _assumption(name: str, base_mu: float, *, shift_scale: float) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family="normal",
        current_mu=base_mu,
        current_sigma=0.05,
        base_mu=base_mu,
        base_sigma=0.05,
        shift_scale=ScaleSpec(center=shift_scale, uncertainty=0.0),
        constraints={},
        active=True,
    )


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    ("assumption", "factor_value"),
    (
        (_assumption("OPERATING_MARGIN", 0.55, shift_scale=0.25), 1.5),
        (_assumption("SALES_TO_CAPITAL_RATIO", 2.0, shift_scale=0.50), 1.5),
        (_assumption("WACC", 0.10, shift_scale=0.25), -1.5),
        (_assumption("ROIC", 0.45, shift_scale=0.10), 1.5),
    ),
)
def test_loading_and_mc_mu_paths_are_byte_equal_across_stages(
    stage: LifecycleStage, assumption: AssumptionState, factor_value: float
) -> None:
    factor_values = {name: factor_value for name in FACTOR_NAMES}

    loading_mu = _loading_mu(assumption, factor_values, stage=stage)
    mc_mu = _current_mc_mu(assumption, factor_values)

    assert _double_bytes(loading_mu) == _double_bytes(mc_mu)


def test_loading_path_preserves_narrative_shift_cap_before_reversion() -> None:
    assumption = _assumption("OPERATING_MARGIN", 0.55, shift_scale=0.25)
    factor_values = {name: 1.5 for name in FACTOR_NAMES}
    loading = LOADING[assumption.name]
    raw_shift = (
        sum(loading[name] * factor_values[name] for name in loading)
        * assumption.shift_scale.center
    )
    cap = NARRATIVE_SHIFT_CAPS[assumption.name][1]

    assert raw_shift > cap
    assert narrative_shift_for_assumption(assumption, factor_values) == cap

    actual = _loading_mu(assumption, factor_values, stage="growth")
    capped = assumption.with_mu(assumption.base_mu + cap)
    expected = apply_constraints(
        apply_mean_reversion(capped, t_year=2.0, company=_company()),
        assumption,
        _company(),
    )
    assert _double_bytes(actual) == _double_bytes(expected)


def test_loading_path_preserves_wacc_band_constraint() -> None:
    assumption = _assumption("WACC", 0.10, shift_scale=0.25)
    factor_values = {name: -1.5 for name in FACTOR_NAMES}

    actual = _loading_mu(assumption, factor_values, stage="mature")

    assert _double_bytes(actual) == _double_bytes(assumption.base_mu + 0.015)


@given(
    stage=st.sampled_from(STAGES),
    assumption_name=st.sampled_from(tuple(LOADING)),
    factor_value=st.floats(
        min_value=-1.5,
        max_value=1.5,
        allow_nan=False,
        allow_infinity=False,
    ),
    shift_scale=st.floats(
        min_value=0.01,
        max_value=0.50,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_loading_and_mc_mu_paths_agree_for_loading_range(
    stage: LifecycleStage,
    assumption_name: str,
    factor_value: float,
    shift_scale: float,
) -> None:
    assumption = _assumption(assumption_name, 0.20, shift_scale=shift_scale)
    factor_values = {name: factor_value for name in FACTOR_NAMES}

    loading_mu = _loading_mu(assumption, factor_values, stage=stage)
    mc_mu = _current_mc_mu(assumption, factor_values)
    narrative_shift = narrative_shift_for_assumption(assumption, factor_values)
    low, high = NARRATIVE_SHIFT_CAPS.get(assumption_name, (-math.inf, math.inf))

    assert _double_bytes(loading_mu) == _double_bytes(mc_mu)
    assert low <= narrative_shift <= high
    if assumption_name == "WACC":
        assert assumption.base_mu - 0.015 <= loading_mu <= assumption.base_mu + 0.015
    assert math.isfinite(loading_mu)


def _loading_mu(
    assumption: AssumptionState,
    factor_values: Mapping[str, float],
    *,
    stage: LifecycleStage,
) -> float:
    factors = {
        name: FactorState(name=name, current_value=value) for name, value in factor_values.items()
    }
    return apply_factor_loadings(
        [assumption],
        factors,
        stage=stage,
        company=_company(),
        t_year=2.0,
    )[assumption.name].current_mu


def _current_mc_mu(
    assumption: AssumptionState, factor_values: Mapping[str, float]
) -> float:
    shifted_mu = shifted_mu_from_factors(assumption, factor_values)
    reverted_mu = apply_mean_reversion(
        assumption.with_mu(shifted_mu), t_year=2.0, company=_company()
    )
    return apply_constraints(reverted_mu, assumption, _company())


def _company() -> dict[str, float]:
    return {
        "operating_margin": 0.56,
        "tax_rate": 0.13,
        "wacc_estimate": 0.10,
        "competitive_advantage_score": 0.8,
        "industry_top_decile": 0.70,
        "statutory_tax_rate": 0.21,
    }


def _double_bytes(value: float) -> bytes:
    return struct.pack("!d", value)
