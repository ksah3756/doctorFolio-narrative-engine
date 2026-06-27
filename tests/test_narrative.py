import numpy as np
import pytest

from dcf_engine.assumption import AssumptionState, ReinvestmentTool, ScaleSpec
from dcf_engine.distributions import DistributionFamily
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.narrative import (
    DEFAULT_NARRATIVE_ID,
    Narrative,
    NarrativeContainer,
    NarrativeScenarioSet,
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
    stage: LifecycleStage,
    tool: ReinvestmentTool,
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


def test_claim_activation_mask_uses_temporary_extraction_modalities() -> None:
    from dcf_engine.extraction.client import _extraction_payload_from_content

    payload = _extraction_payload_from_content(
        """
        {
          "claims": [
            {
              "claim_id": "reported-revenue",
              "claim_text": "Revenue increased year over year.",
              "claim_subject": "DEMAND_SIGNAL",
              "claim_nature": "REALIZED",
              "direction": "INCREASE",
              "magnitude_qualifier": "STRONG",
              "macro_variable": null,
              "instrument_type": null,
              "extraction_quality": {
                "verbatim_overlap": 0.9,
                "numeric_consistency": true,
                "temporal_consistency": true,
                "entity_consistency": true
              },
              "source_ref": {
                "discovery_channel": "edgar_api",
                "content_source": "10-Q",
                "source_reliability": 0.95
              },
              "chunk_ref": "chunk-1",
              "published_date": "2026-05-20"
            },
            {
              "claim_id": "management-readthrough",
              "claim_text": "Management commentary implies durable demand.",
              "claim_subject": "DEMAND_SIGNAL",
              "claim_nature": "GUIDANCE",
              "direction": "INCREASE",
              "magnitude_qualifier": "MODERATE",
              "macro_variable": null,
              "instrument_type": null,
              "extraction_quality": {
                "verbatim_overlap": 0.9,
                "numeric_consistency": true,
                "temporal_consistency": true,
                "entity_consistency": true
              },
              "source_ref": {
                "discovery_channel": "edgar_api",
                "content_source": "10-Q",
                "source_reliability": 0.95
              },
              "chunk_ref": "chunk-1",
              "published_date": "2026-05-20"
            },
            {
              "claim_id": "five-year-share-gain",
              "claim_text": "The company could gain share over five years.",
              "claim_subject": "COMPETITIVE_POSITION",
              "claim_nature": "STRUCTURAL",
              "direction": "INCREASE",
              "magnitude_qualifier": "MODERATE",
              "macro_variable": null,
              "instrument_type": null,
              "extraction_quality": {
                "verbatim_overlap": 0.9,
                "numeric_consistency": true,
                "temporal_consistency": true,
                "entity_consistency": true
              },
              "source_ref": {
                "discovery_channel": "edgar_api",
                "content_source": "10-Q",
                "source_reliability": 0.95
              },
              "chunk_ref": "chunk-1",
              "published_date": "2026-05-20"
            }
          ],
          "claim_modalities": {
            "reported-revenue": "FACT",
            "management-readthrough": "INTERPRETATION",
            "five-year-share-gain": "PROJECTION"
          }
        }
        """
    )

    mask = build_claim_activation_mask(
        claim_modalities=payload.claim_modalities,
        selected_claim_ids={"five-year-share-gain"},
    )

    assert mask == {
        "reported-revenue": True,
        "management-readthrough": False,
        "five-year-share-gain": True,
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


@pytest.mark.parametrize(
    "probabilities_by_narrative",
    [
        {"base": -0.1, "bull": 1.1},
        {"base": float("nan"), "bull": 1.0},
        {"base": float("inf"), "bull": 0.0},
        {"base": 0.3, "bull": 0.6},
    ],
)
def test_scenario_set_rejects_invalid_probabilities(
    probabilities_by_narrative: dict[str, float],
) -> None:
    containers = [
        _container("base", _valuation_assumptions()),
        _container("bull", _valuation_assumptions()),
    ]

    with pytest.raises(ValueError, match="probabilities"):
        NarrativeScenarioSet.from_containers(
            containers=containers,
            probabilities_by_narrative=probabilities_by_narrative,
        )


@pytest.mark.parametrize(
    "values_by_narrative",
    [
        {"base": 100.0},
        {"base": 100.0, "bull": 140.0, "bear": 70.0},
        {"base": 100.0, "bear": 70.0},
    ],
)
def test_scenario_value_maps_reject_mismatched_narrative_ids(
    values_by_narrative: dict[str, float],
) -> None:
    scenario_set = NarrativeScenarioSet.from_containers(
        containers=[
            _container("base", _valuation_assumptions()),
            _container("bull", _valuation_assumptions()),
        ],
        probabilities_by_narrative={"base": 0.75, "bull": 0.25},
    )

    with pytest.raises(ValueError, match="values_by_narrative"):
        scenario_set.probability_weighted_value(values_by_narrative)


@pytest.mark.parametrize(
    ("base_stage", "peer_id", "peer_stage", "base_tam", "peer_tam"),
    [
        ("growth", "mature", "mature", {}, {}),
        ("growth", "supplier", "growth", {"market": "platform"}, {"market": "supplier"}),
    ],
)
def test_scenario_set_rejects_type_2_measurement_axis_mixing(
    base_stage: LifecycleStage,
    peer_id: str,
    peer_stage: LifecycleStage,
    base_tam: dict[str, object],
    peer_tam: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="measurement axis"):
        NarrativeScenarioSet.from_containers(
            containers=[
                _container(
                    "base",
                    _valuation_assumptions(),
                    lifecycle_stage=base_stage,
                    tam_structure=base_tam,
                ),
                _container(
                    peer_id,
                    _valuation_assumptions(),
                    lifecycle_stage=peer_stage,
                    tam_structure=peer_tam,
                ),
            ],
            probabilities_by_narrative={"base": 0.50, peer_id: 0.50},
        )


def test_probability_weighted_value_rejects_non_scalar_shape_mismatch() -> None:
    scenario_set = NarrativeScenarioSet.from_containers(
        containers=[
            _container("base", _valuation_assumptions()),
            _container("bull", _valuation_assumptions()),
        ],
        probabilities_by_narrative={"base": 0.50, "bull": 0.50},
    )

    with pytest.raises(ValueError, match="shape"):
        scenario_set.probability_weighted_value(
            {
                "base": np.array([100.0, 110.0], dtype=np.float64),
                "bull": np.array([[120.0], [130.0]], dtype=np.float64),
            }
        )


def test_bull_base_bear_scenario_set_returns_probability_weighted_value() -> None:
    scenario_set = NarrativeScenarioSet.from_containers(
        containers=[
            _container("bear", _valuation_assumptions()),
            _container("base", _valuation_assumptions()),
            _container("bull", _valuation_assumptions()),
        ],
        probabilities_by_narrative={"bear": 0.20, "base": 0.50, "bull": 0.30},
    )

    weighted_value = scenario_set.probability_weighted_value(
        {"bear": 70.0, "base": 100.0, "bull": 150.0}
    )

    assert weighted_value == pytest.approx(109.0)


def test_single_container_scenario_set_matches_existing_base_path() -> None:
    assumptions = _valuation_assumptions()
    container = NarrativeContainer.single(assumptions=assumptions)
    direct_value = going_concern_value_samples(
        initial_revenue=100.0,
        assumption_samples=_constant_samples(assumptions),
        forecast_years=3,
    )

    scenario_set = NarrativeScenarioSet.single(container)
    weighted_value = scenario_set.probability_weighted_value(
        {container.narrative.narrative_id: direct_value}
    )

    np.testing.assert_array_equal(weighted_value, direct_value)


def _valuation_assumptions() -> list[AssumptionState]:
    return [
        _assumption("REVENUE_CAGR", 0.08, 0.01, "normal"),
        _assumption("OPERATING_MARGIN", 0.20, 0.01, "normal"),
        _assumption("TAX_RATE", 0.20, 0.005, "normal"),
        _assumption("SALES_TO_CAPITAL_RATIO", 2.0, 0.05, "lognormal"),
        _assumption("WACC", 0.10, 0.002, "normal"),
        _assumption("TERMINAL_GROWTH", 0.02, 0.001, "normal"),
    ]


def _container(
    narrative_id: str,
    assumptions: list[AssumptionState],
    *,
    lifecycle_stage: LifecycleStage = "growth",
    tam_structure: dict[str, object] | None = None,
) -> NarrativeContainer:
    return NarrativeContainer.single(
        narrative=Narrative.default(
            narrative_id=narrative_id,
            lifecycle_stage=lifecycle_stage,
            tam_structure=tam_structure,
        ),
        assumptions=assumptions,
    )


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
