import numpy as np
import pytest

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.distributions import DistributionFamily
from dcf_engine.narrative import (
    DEFAULT_NARRATIVE_ID,
    Narrative,
    NarrativeContainer,
    build_claim_activation_mask,
    create_narrative,
)
from dcf_engine.projection import going_concern_value_samples


def test_default_single_narrative_container_has_stable_id_and_owned_state() -> None:
    narrative = Narrative.default()

    assert narrative.narrative_id == DEFAULT_NARRATIVE_ID
    assert narrative.lifecycle_stage == "growth"
    assert narrative.tam_structure == {}
    assert narrative.claim_activation_mask == {}


@pytest.mark.parametrize(
    ("stage", "tool"),
    [
        ("young", "sales_to_capital"),
        ("growth", "sales_to_capital"),
        ("mature", "roic"),
        ("decline", "roic"),
    ],
)
def test_narrative_derives_reinvestment_tool_from_lifecycle_stage(
    stage: str,
    tool: str,
) -> None:
    narrative = Narrative.default(lifecycle_stage=stage)

    assert narrative.reinvestment_tool == tool


def test_narrative_rejects_independent_reinvestment_override() -> None:
    with pytest.raises(ValueError, match="lifecycle_stage"):
        create_narrative(lifecycle_stage="growth", reinvestment_model="roic")


def test_container_stores_and_retrieves_assumptions_by_narrative_id() -> None:
    assumptions = [_assumption("REVENUE_CAGR", 0.08, 0.01, "normal")]
    container = NarrativeContainer.single(assumptions=assumptions)

    assert set(container.assumptions_by_narrative) == {DEFAULT_NARRATIVE_ID}
    assert container.assumptions_for(DEFAULT_NARRATIVE_ID) == assumptions
    assert container.active_assumptions == assumptions


def test_claim_activation_mask_keeps_facts_active_and_selects_non_facts() -> None:
    mask = build_claim_activation_mask(
        claim_modalities={
            "reported_revenue": "FACT",
            "platform_readthrough": "INTERPRETATION",
            "five_year_share_gain": "PROJECTION",
        },
        selected_claim_ids={"five_year_share_gain"},
    )

    assert mask == {
        "reported_revenue": True,
        "platform_readthrough": False,
        "five_year_share_gain": True,
    }


def test_narrative_container_wraps_current_dcf_path_without_changing_behavior() -> None:
    assumptions = _valuation_assumptions()
    direct_samples = _constant_samples(assumptions)
    container = NarrativeContainer.single(assumptions=assumptions)
    wrapped_samples = _constant_samples(container.active_assumptions)

    direct_value = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=direct_samples,
        forecast_years=3,
    )
    wrapped_value = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=wrapped_samples,
        forecast_years=3,
        stage=container.narrative.lifecycle_stage,
    )

    np.testing.assert_array_equal(wrapped_value, direct_value)


def _valuation_assumptions() -> list[AssumptionState]:
    return [
        _assumption("REVENUE_CAGR", 0.08, 0.01, "normal"),
        _assumption("OPERATING_MARGIN", 0.20, 0.01, "normal"),
        _assumption("TAX_RATE", 0.20, 0.005, "normal"),
        _assumption("SALES_TO_CAPITAL_RATIO", 2.0, 0.05, "lognormal"),
        _assumption("WACC", 0.10, 0.002, "normal"),
        _assumption("TERMINAL_GROWTH", 0.02, 0.001, "normal"),
    ]


def _constant_samples(assumptions: list[AssumptionState]) -> dict[str, np.ndarray]:
    return {
        assumption.name: np.full((4,), assumption.base_mu, dtype=np.float64)
        for assumption in assumptions
    }


def _assumption(
    name: str,
    mu: float,
    sigma: float,
    family: DistributionFamily,
) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=mu,
        current_sigma=sigma,
        base_mu=mu,
        base_sigma=sigma,
        shift_scale=ScaleSpec(center=0.05, uncertainty=0.0),
        constraints={"low": 0.0, "high": 1.0},
        active=True,
    )
