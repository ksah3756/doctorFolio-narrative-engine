from datetime import UTC, datetime

import numpy as np
import pytest

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.factor import FactorState
from dcf_engine.monte_carlo import MonteCarloConfig, mc_iteration_with_validation, mc_run


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


def _assumption(name: str, mu: float, sigma: float, family: str) -> AssumptionState:
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
