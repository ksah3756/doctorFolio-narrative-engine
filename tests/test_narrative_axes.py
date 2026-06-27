import numpy as np
import pytest

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.distributions import DistributionFamily
from dcf_engine.narrative import NarrativeScenarioSet
from dcf_engine.narrative_axes import (
    NarrativeAxis,
    PullSignature,
    generate_narrative_axes,
    generate_type1_narrative_candidates,
)


def test_rejects_empty_signature_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        generate_narrative_axes(())


def test_rejects_signature_vectors_with_mismatched_shapes() -> None:
    signatures = (
        PullSignature(assumption_id="revenue_cagr", values=(0.20, 0.10)),
        PullSignature(assumption_id="operating_margin", values=(0.30,)),
    )

    with pytest.raises(ValueError, match="same shape"):
        generate_narrative_axes(signatures)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nan_or_infinite_signature_values(bad_value: float) -> None:
    signatures = (
        PullSignature(assumption_id="revenue_cagr", values=(0.20, bad_value)),
        PullSignature(assumption_id="operating_margin", values=(0.30, 0.40)),
    )

    with pytest.raises(ValueError, match="finite"):
        generate_narrative_axes(signatures)


def test_recovers_dominant_component_for_synthetic_one_axis_contested_set() -> None:
    signatures = (
        PullSignature(assumption_id="a_revenue_cagr", values=(3.0, 0.0)),
        PullSignature(assumption_id="b_operating_margin", values=(2.0, 0.0)),
        PullSignature(assumption_id="c_wacc", values=(-1.0, 0.0)),
    )

    axes = generate_narrative_axes(signatures)

    assert len(axes) == 1
    assert axes[0].explained_variance_ratio == pytest.approx(1.0)
    expected_scale = np.sqrt(14.0)
    assert axes[0].loadings == pytest.approx(
        {
            "a_revenue_cagr": 3.0 / expected_scale,
            "b_operating_margin": 2.0 / expected_scale,
            "c_wacc": -1.0 / expected_scale,
        }
    )


def test_respects_explained_variance_threshold_and_max_axes_cap() -> None:
    signatures = (
        PullSignature(assumption_id="a_revenue_cagr", values=(3.0, 0.0, 0.0)),
        PullSignature(assumption_id="b_operating_margin", values=(0.0, 2.0, 0.0)),
        PullSignature(assumption_id="c_wacc", values=(0.0, 0.0, 1.0)),
    )

    axes = generate_narrative_axes(
        signatures,
        explained_variance_threshold=0.95,
        max_axes=2,
    )

    assert len(axes) == 2
    assert sum(axis.explained_variance_ratio for axis in axes) == pytest.approx(13.0 / 14.0)


def test_uses_deterministic_component_orientation_for_repeated_runs() -> None:
    signatures = (
        PullSignature(assumption_id="b_operating_margin", values=(-2.0, 0.0)),
        PullSignature(assumption_id="a_revenue_cagr", values=(-3.0, 0.0)),
        PullSignature(assumption_id="c_wacc", values=(1.0, 0.0)),
    )

    first_axes = generate_narrative_axes(signatures)
    second_axes = generate_narrative_axes(signatures)

    assert first_axes == second_axes
    assert first_axes[0].loadings["a_revenue_cagr"] > 0.0


def test_preserves_stable_assumption_id_ordering_in_returned_axis_loadings() -> None:
    signatures = (
        PullSignature(assumption_id="wacc", values=(1.0, 0.0)),
        PullSignature(assumption_id="revenue_cagr", values=(2.0, 0.0)),
        PullSignature(assumption_id="operating_margin", values=(3.0, 0.0)),
    )

    axes = generate_narrative_axes(signatures)

    assert tuple(axes[0].loadings) == (
        "operating_margin",
        "revenue_cagr",
        "wacc",
    )


def test_narrative_axis_creates_positive_and_negative_type1_candidates() -> None:
    axis = NarrativeAxis(
        axis_index=1,
        explained_variance_ratio=0.72,
        loadings={"REVENUE_CAGR": 0.50, "OPERATING_MARGIN": -0.25},
    )
    assumptions = (
        _assumption("REVENUE_CAGR", 0.10, shift_scale=0.04),
        _assumption("OPERATING_MARGIN", 0.20, shift_scale=0.08),
        _assumption("WACC", 0.09, shift_scale=0.01),
    )

    candidates = generate_type1_narrative_candidates(
        axis,
        assumptions=assumptions,
        shift_strength=2.0,
    )

    assert tuple(container.narrative.narrative_id for container in candidates) == (
        "type1-axis-1-positive",
        "type1-axis-1-negative",
    )
    positive = {
        assumption.name: assumption
        for assumption in candidates[0].active_assumptions
    }
    negative = {
        assumption.name: assumption
        for assumption in candidates[1].active_assumptions
    }
    assert tuple(positive) == ("REVENUE_CAGR", "OPERATING_MARGIN", "WACC")
    assert positive["REVENUE_CAGR"].current_mu == pytest.approx(0.14)
    assert negative["REVENUE_CAGR"].current_mu == pytest.approx(0.06)
    assert positive["OPERATING_MARGIN"].current_mu == pytest.approx(0.16)
    assert negative["OPERATING_MARGIN"].current_mu == pytest.approx(0.24)
    assert positive["WACC"].current_mu == pytest.approx(0.09)
    assert negative["WACC"].current_mu == pytest.approx(0.09)


def test_generated_type1_candidates_preserve_one_measurement_axis_for_scenario_sets() -> None:
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"REVENUE_CAGR": 1.0},
    )
    tam_structure = {"market": "ai-accelerators", "segments": ("training", "inference")}

    candidates = generate_type1_narrative_candidates(
        axis,
        assumptions=(_assumption("REVENUE_CAGR", 0.10, shift_scale=0.02),),
        lifecycle_stage="mature",
        tam_structure=tam_structure,
    )
    scenario_set = NarrativeScenarioSet.from_containers(
        containers=candidates,
        probabilities_by_narrative={
            "type1-axis-0-positive": 0.50,
            "type1-axis-0-negative": 0.50,
        },
    )

    assert scenario_set.narrative_ids == (
        "type1-axis-0-positive",
        "type1-axis-0-negative",
    )
    for container in candidates:
        assert container.narrative.lifecycle_stage == "mature"
        assert container.narrative.tam_structure == tam_structure


def test_rejects_empty_axis_loadings_for_type1_candidate_generation() -> None:
    axis = NarrativeAxis(axis_index=0, explained_variance_ratio=1.0, loadings={})

    with pytest.raises(ValueError, match="loadings"):
        generate_type1_narrative_candidates(
            axis,
            assumptions=(_assumption("REVENUE_CAGR", 0.10),),
        )


@pytest.mark.parametrize("bad_loading", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_axis_loadings_for_type1_candidate_generation(
    bad_loading: float,
) -> None:
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"REVENUE_CAGR": bad_loading},
    )

    with pytest.raises(ValueError, match="finite"):
        generate_type1_narrative_candidates(
            axis,
            assumptions=(_assumption("REVENUE_CAGR", 0.10),),
        )


@pytest.mark.parametrize("bad_shift_strength", [0.0, -1.0])
def test_rejects_zero_or_negative_type1_candidate_shift_strength(
    bad_shift_strength: float,
) -> None:
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"REVENUE_CAGR": 1.0},
    )

    with pytest.raises(ValueError, match="shift_strength"):
        generate_type1_narrative_candidates(
            axis,
            assumptions=(_assumption("REVENUE_CAGR", 0.10),),
            shift_strength=bad_shift_strength,
        )


def _assumption(
    name: str,
    mu: float,
    *,
    shift_scale: float = 0.05,
    family: DistributionFamily = "normal",
) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=mu,
        current_sigma=0.01,
        base_mu=mu,
        base_sigma=0.01,
        shift_scale=ScaleSpec(center=shift_scale, uncertainty=0.0),
        constraints={"low": 0.0, "high": 1.0},
        active=True,
    )
