from datetime import UTC, datetime
from collections.abc import Mapping

import numpy as np
import pytest
from numpy.random import Generator

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.distributions import DistributionFamily
from dcf_engine.factor import FactorState, Regime
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.loading import LOADING
from dcf_engine.monte_carlo import (
    MonteCarloConfig,
    _shifted_mu,
    mc_iteration_with_validation,
    mc_run,
)
from dcf_engine import monte_carlo as monte_carlo_module


def test_rejection_sampling_drops_unrealistic_imputed_roic() -> None:
    assumptions = [
        _assumption("OPERATING_MARGIN", 0.80, 0.01, "normal"),
        _assumption("TAX_RATE", 0.10, 0.01, "normal"),
        _assumption("SALES_TO_CAPITAL_RATIO", 3.0, 0.01, "normal"),
    ]

    sampled = mc_iteration_with_validation(
        factor_states={},
        assumptions=assumptions,
        stage="growth",
        regime="normal",
        company=_company(),
        config=MonteCarloConfig(seed=7, max_resample=1),
    )

    assert sampled is None


def test_mc_run_is_seed_deterministic_and_tracks_reject_rate() -> None:
    assumptions = [_assumption("REVENUE_CAGR", 0.25, 0.03, "normal")]
    factors = {"DemandStrength": FactorState(name="DemandStrength", current_value=0.5)}
    config = MonteCarloConfig(iterations=25, seed=123, now=datetime(2026, 6, 1, tzinfo=UTC))

    one = mc_run(factors, assumptions, "growth", "normal", _company(), config)
    two = mc_run(factors, assumptions, "growth", "normal", _company(), config)

    assert one.reject_rate == pytest.approx(0.0)
    np.testing.assert_allclose(one.samples["REVENUE_CAGR"], two.samples["REVENUE_CAGR"])
    np.testing.assert_array_equal(one.accepted_indices, np.arange(config.iterations))


def test_mc_run_reports_outer_indices_for_accepted_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = iter([None, {"REVENUE_CAGR": 0.10}, {"REVENUE_CAGR": 0.20}])

    def fake_iteration_with_rng(
        *,
        factor_states: Mapping[str, FactorState],
        assumptions: list[AssumptionState],
        stage: LifecycleStage,
        regime: Regime,
        company: Mapping[str, float],
        rng: Generator,
        max_resample: int,
    ) -> dict[str, float] | None:
        return next(calls)

    monkeypatch.setattr(monte_carlo_module, "_iteration_with_rng", fake_iteration_with_rng)

    result = mc_run({}, [_assumption("REVENUE_CAGR", 0.25, 0.03, "normal")], "growth", "normal", _company(), MonteCarloConfig(iterations=3))

    assert result.reject_rate == pytest.approx(1 / 3)
    np.testing.assert_array_equal(result.accepted_indices, np.array([1, 2]))


def test_shifted_mu_uses_canonical_financial_strength_loading() -> None:
    assumption = _assumption("WACC", 0.10, 0.01, "normal")

    shifted = _shifted_mu(assumption, {"FinancialStrength": 1.0})

    assert shifted == pytest.approx(
        assumption.base_mu
        + LOADING["WACC"]["FinancialStrength"] * assumption.shift_scale.center
    )


def _assumption(name: str, mu: float, sigma: float, family: DistributionFamily) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=mu,
        current_sigma=sigma,
        base_mu=mu,
        base_sigma=sigma,
        shift_scale=ScaleSpec(center=0.05, uncertainty=0.0),
        constraints={},
        active=True,
    )


def _company() -> dict[str, float]:
    return {
        "operating_margin": 0.56,
        "tax_rate": 0.13,
        "wacc_estimate": 0.10,
        "competitive_advantage_score": 0.8,
        "industry_top_decile": 0.70,
        "statutory_tax_rate": 0.21,
    }
