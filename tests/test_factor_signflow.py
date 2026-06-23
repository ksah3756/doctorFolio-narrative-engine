from datetime import date

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.claim import (
    Claim,
    ClaimDirection,
    ClaimNature,
    ClaimSubject,
    ExtractionQuality,
    MagnitudeQualifier,
    SourceRef,
)
from dcf_engine.distributions import DistributionFamily
from dcf_engine.factor import FactorState
from dcf_engine.loading import apply_factor_loadings
from dcf_engine.monte_carlo import MonteCarloConfig, mc_run
from dcf_engine.routing import ROUTING, factor_shift, route_claims_to_factors

MAGNITUDES: tuple[MagnitudeQualifier, ...] = ("WEAK", "MODERATE", "STRONG", "EXTREME")
DIRECTIONS: tuple[ClaimDirection, ...] = ("INCREASE", "DECREASE")
COMPANY: dict[str, float] = {
    "operating_margin": 0.30,
    "tax_rate": 0.20,
    "wacc_estimate": 0.10,
    "competitive_advantage_score": 0.80,
    "industry_top_decile": 0.50,
    "statutory_tax_rate": 0.21,
}


def test_governance_increase_routes_to_positive_execution_quality() -> None:
    factors = route_claims_to_factors([_claim("GOVERNANCE", "INCREASE")], "mature")

    assert factors["ExecutionQuality"].current_value > 0.0


def test_governance_execution_quality_raises_sales_to_capital_mu() -> None:
    assumption = _assumption("SALES_TO_CAPITAL_RATIO", 2.80, 0.10)
    factors = route_claims_to_factors([_claim("GOVERNANCE", "INCREASE")], "mature")

    baseline = _loaded_mu(assumption, {})
    shifted = _loaded_mu(assumption, factors)

    assert factors["ExecutionQuality"].current_value > 0.0
    assert shifted > baseline


def test_financial_health_decrease_inverts_default_probability_mu_sign() -> None:
    # base_mu는 NARRATIVE_DEFAULT_PROBABILITY_CAP(0.05) 안쪽으로 둔다.
    # 0.20처럼 cap 위면 baseline·shifted가 모두 0.05로 눌려 방향성이 안 보인다.
    assumption = _assumption("DEFAULT_PROBABILITY", 0.03, 0.01)
    factors = route_claims_to_factors([_claim("FINANCIAL_HEALTH", "DECREASE")], "mature")

    baseline = _loaded_mu(assumption, {})
    shifted = _loaded_mu(assumption, factors)

    assert factors["FinancialStrength"].current_value < 0.0
    assert shifted > baseline


def test_governance_increase_lowers_default_probability_mu() -> None:
    assumption = _assumption("DEFAULT_PROBABILITY", 0.03, 0.01)
    factors = route_claims_to_factors([_claim("GOVERNANCE", "INCREASE")], "mature")

    baseline = _loaded_mu(assumption, {})
    shifted = _loaded_mu(assumption, factors)

    assert factors["ExecutionQuality"].current_value > 0.0
    assert shifted < baseline


def test_claim_signs_reach_reproducible_finite_monte_carlo_paths() -> None:
    factors = route_claims_to_factors(
        [
            _claim("GOVERNANCE", "INCREASE"),
            _claim("FINANCIAL_HEALTH", "DECREASE"),
        ],
        "mature",
    )
    baseline_factors = {
        name: FactorState(name=name, current_value=0.0) for name in factors
    }
    assumptions = [
        _assumption("SALES_TO_CAPITAL_RATIO", 2.80, 0.0),
        _assumption("DEFAULT_PROBABILITY", 0.03, 0.0),
    ]
    config = MonteCarloConfig(iterations=128, seed=20260623, t_year=1.0)

    baseline = mc_run(baseline_factors, assumptions, "mature", "normal", COMPANY, config)
    first = mc_run(factors, assumptions, "mature", "normal", COMPANY, config)
    second = mc_run(factors, assumptions, "mature", "normal", COMPANY, config)

    assert np.all(
        first.samples["SALES_TO_CAPITAL_RATIO"]
        > baseline.samples["SALES_TO_CAPITAL_RATIO"]
    )
    # DEFAULT_PROBABILITY는 0.05 cap 근처로 압축돼 표본별 엄격 순서는 factor sampling noise로
    # 깨질 수 있다. 부호(집계 방향)는 평균으로 확인한다.
    assert (
        first.samples["DEFAULT_PROBABILITY"].mean()
        > baseline.samples["DEFAULT_PROBABILITY"].mean()
    )
    for name, samples in first.samples.items():
        assert np.isfinite(samples).all(), name
        np.testing.assert_array_equal(samples, second.samples[name])
    np.testing.assert_array_equal(first.accepted_indices, second.accepted_indices)


@given(
    direction=st.sampled_from(DIRECTIONS),
    magnitude=st.sampled_from(MAGNITUDES),
    reliability=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_governance_factor_shift_sign_follows_claim_direction_within_cap(
    direction: ClaimDirection,
    magnitude: MagnitudeQualifier,
    reliability: float,
) -> None:
    claim = _claim(
        "GOVERNANCE",
        direction,
        magnitude=magnitude,
        reliability=reliability,
        nature="STRUCTURAL",
    )

    shift = factor_shift(claim, ROUTING["GOVERNANCE"]["ExecutionQuality"], "young")

    assert abs(shift) <= 1.5
    if direction == "INCREASE":
        assert shift >= 0.0
    else:
        assert shift <= 0.0


def _loaded_mu(assumption: AssumptionState, factors: dict[str, FactorState]) -> float:
    shifted = apply_factor_loadings(
        [assumption],
        factors,
        stage="mature",
        company=COMPANY,
        t_year=1.0,
    )
    return shifted[assumption.name].current_mu


def _claim(
    subject: ClaimSubject,
    direction: ClaimDirection,
    *,
    magnitude: MagnitudeQualifier = "STRONG",
    reliability: float = 0.95,
    nature: ClaimNature = "REALIZED",
) -> Claim:
    return Claim(
        claim_id=f"{subject}-{direction}-{magnitude}",
        claim_text="Narrative sign-flow evidence.",
        claim_subject=subject,
        claim_nature=nature,
        direction=direction,
        magnitude_qualifier=magnitude,
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.95,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=SourceRef(
            discovery_channel="direct",
            content_source="signflow_test_source",
            source_reliability=reliability,
        ),
        chunk_ref="signflow-test-chunk",
        published_date=date(2026, 6, 23),
    )


def _assumption(
    name: str,
    mu: float,
    sigma: float,
    family: DistributionFamily = "normal",
) -> AssumptionState:
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
