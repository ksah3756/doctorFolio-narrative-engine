import math

import numpy as np
import pytest
from pydantic import ValidationError

from dcf_engine.mature_case import (
    AnnualObservation,
    MatureCaseResult,
    MatureHistory,
    run_mature_case,
)


def test_history_requires_exactly_five_immutable_observations() -> None:
    history = _history()

    assert len(history.observations) == 5
    assert AnnualObservation.model_config["frozen"] is True
    assert MatureHistory.model_config["frozen"] is True
    with pytest.raises(ValidationError, match="exactly five"):
        MatureHistory(observations=history.observations[:-1])
    with pytest.raises(ValidationError, match="exactly five"):
        MatureHistory(observations=(*history.observations, history.observations[-1]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revenue_growth", math.nan),
        ("revenue_growth", -1.0),
        ("operating_margin", math.inf),
        ("operating_margin", -1.0),
        ("roic", 0.0),
        ("wacc", 1.0),
        ("tax_rate", -0.01),
        ("nopat", 0.0),
    ],
)
def test_annual_observation_rejects_non_finite_and_domain_invalid_values(
    field: str, value: float
) -> None:
    values = {
        "revenue_growth": 0.04,
        "operating_margin": 0.20,
        "roic": 0.16,
        "wacc": 0.09,
        "tax_rate": 0.21,
        "nopat": 120.0,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        AnnualObservation(**values)


def test_mature_case_derives_history_anchors_and_lifecycle_contract() -> None:
    history = _history()
    result = run_mature_case(history, seed=20260624, iterations=256)

    assert result.stage == "mature"
    assert result.valuation_mode == "established"
    assert result.operating_margin_base_mu == pytest.approx(0.20)
    assert result.roic_base_mu == pytest.approx(0.16)
    assert result.wacc_base_mu == pytest.approx(0.09)
    assert all(item.operating_margin != 0.20 for item in history.observations)
    assert all(item.roic != 0.16 for item in history.observations)
    assert all(item.wacc != 0.09 for item in history.observations)
    assert "ROIC" in result.active_assumptions
    assert "SALES_TO_CAPITAL_RATIO" in result.inactive_assumptions
    assert "SALES_TO_CAPITAL_RATIO" not in result.active_assumptions


@pytest.mark.parametrize("field", ["operating_margin", "roic", "wacc"])
def test_each_history_year_contributes_to_each_anchor_mean(field: str) -> None:
    history = _history()
    baseline = run_mature_case(history, seed=20260624, iterations=8)
    delta = 0.005

    for index, observation in enumerate(history.observations):
        observations = list(history.observations)
        observations[index] = _with_anchor_delta(observation, field, delta)

        perturbed = run_mature_case(
            MatureHistory(observations=tuple(observations)),
            seed=20260624,
            iterations=8,
        )

        assert _anchor_base_mu(perturbed, field) == pytest.approx(
            _anchor_base_mu(baseline, field) + delta / len(observations)
        )


def test_mature_case_rejects_non_positive_iterations() -> None:
    with pytest.raises(ValueError, match="iterations must be at least 1"):
        run_mature_case(_history(), iterations=0)


def test_mature_claims_reach_reproducible_reinvestment_samples() -> None:
    history = _history()
    first = run_mature_case(history, seed=20260624, iterations=256)
    second = run_mature_case(history, seed=20260624, iterations=256)

    assert first.narrative_sensitivity == pytest.approx(0.8)
    assert first.operating_efficiency_factor < 0.0
    assert first.operating_margin_claim_mu < first.operating_margin_baseline_mu
    assert first.wacc_claim_mu > first.wacc_baseline_mu
    assert first.reject_rate == pytest.approx(0.0)
    assert first.reinvestment_p10 <= first.reinvestment_median <= first.reinvestment_p90

    sample_arrays = (
        first.revenue_growth_samples,
        first.operating_margin_samples,
        first.roic_samples,
        first.wacc_samples,
        first.reinvestment_samples,
    )
    assert all(samples.shape == (256,) for samples in sample_arrays)
    assert all(np.isfinite(samples).all() for samples in sample_arrays)
    assert all(not samples.flags.writeable for samples in sample_arrays)
    np.testing.assert_allclose(
        first.reinvestment_samples,
        history.observations[-1].nopat
        * first.revenue_growth_samples
        / first.roic_samples,
    )
    # 결정성: 고정 seed면 reinvestment뿐 아니라 모든 sample 배열이 정확히 동일해야 한다.
    second_arrays = (
        second.revenue_growth_samples,
        second.operating_margin_samples,
        second.roic_samples,
        second.wacc_samples,
        second.reinvestment_samples,
    )
    for first_samples, second_samples in zip(sample_arrays, second_arrays, strict=True):
        np.testing.assert_array_equal(first_samples, second_samples)


def _history() -> MatureHistory:
    return MatureHistory(
        observations=(
            AnnualObservation(
                revenue_growth=0.030,
                operating_margin=0.17,
                roic=0.13,
                wacc=0.07,
                tax_rate=0.20,
                nopat=100.0,
            ),
            AnnualObservation(
                revenue_growth=0.040,
                operating_margin=0.19,
                roic=0.15,
                wacc=0.08,
                tax_rate=0.21,
                nopat=105.0,
            ),
            AnnualObservation(
                revenue_growth=0.050,
                operating_margin=0.21,
                roic=0.17,
                wacc=0.095,
                tax_rate=0.22,
                nopat=110.0,
            ),
            AnnualObservation(
                revenue_growth=0.040,
                operating_margin=0.22,
                roic=0.18,
                wacc=0.10,
                tax_rate=0.21,
                nopat=115.0,
            ),
            AnnualObservation(
                revenue_growth=0.035,
                operating_margin=0.21,
                roic=0.17,
                wacc=0.105,
                tax_rate=0.21,
                nopat=120.0,
            ),
        )
    )


def _with_anchor_delta(
    observation: AnnualObservation, field: str, delta: float
) -> AnnualObservation:
    if field == "operating_margin":
        return observation.model_copy(
            update={"operating_margin": observation.operating_margin + delta}
        )
    if field == "roic":
        return observation.model_copy(update={"roic": observation.roic + delta})
    if field == "wacc":
        return observation.model_copy(update={"wacc": observation.wacc + delta})
    raise AssertionError(f"unsupported anchor field: {field}")


def _anchor_base_mu(result: MatureCaseResult, field: str) -> float:
    if field == "operating_margin":
        return result.operating_margin_base_mu
    if field == "roic":
        return result.roic_base_mu
    if field == "wacc":
        return result.wacc_base_mu
    raise AssertionError(f"unsupported anchor field: {field}")
