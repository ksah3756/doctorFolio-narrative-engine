from datetime import date

import numpy as np
import pytest

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.claim import (
    Claim,
    ClaimDirection,
    ClaimSubject,
    ExtractionQuality,
    MagnitudeQualifier,
    SourceRef,
)
from dcf_engine.distributions import DistributionFamily
from dcf_engine.loading import resolved_mu
from dcf_engine.narrative import NarrativeScenarioSet
from dcf_engine.narrative_axes import (
    ClaimAssumptionPull,
    ContestedAssumptionPullInput,
    EvidencePull,
    NarrativeAxis,
    PullSignature,
    _axis_stability_score,
    _centered_claim_assumption_matrix,
    _weighted_claim_assumption_pull,
    build_pull_signature,
    build_type1_claim_assumption_pulls,
    evaluate_type1_assumption_mass_gates,
    generate_narrative_axes,
    generate_type1_narrative_candidates,
    generate_type1_tension_axes,
    generate_type1_tension_axis_diagnostics,
)


def test_type1_claim_bridge_maps_demand_increase_to_growth_upside_pulls() -> None:
    pulls = build_type1_claim_assumption_pulls(
        [_claim("DEMAND_SIGNAL", "INCREASE", claim_id="demand-up")],
        stage="growth",
        assumption_ids=("REVENUE_CAGR", "OPERATING_MARGIN"),
    )

    by_assumption = {pull.assumption_id: pull for pull in pulls}

    assert by_assumption["REVENUE_CAGR"].claim_id == "demand-up"
    assert by_assumption["REVENUE_CAGR"].pull > 0.0
    assert by_assumption["OPERATING_MARGIN"].pull > 0.0


def test_type1_claim_bridge_preserves_cost_increase_valuation_signs() -> None:
    pulls = build_type1_claim_assumption_pulls(
        [_claim("COST_SIGNAL", "INCREASE", claim_id="cost-up")],
        stage="growth",
        assumption_ids=("OPERATING_MARGIN", "WACC"),
    )

    by_assumption = {pull.assumption_id: pull for pull in pulls}

    assert by_assumption["OPERATING_MARGIN"].pull < 0.0
    assert by_assumption["WACC"].pull > 0.0


def test_type1_claim_bridge_omits_bridge_only_capital_structure_claims() -> None:
    pulls = build_type1_claim_assumption_pulls(
        [
            _claim(
                "CAPITAL_STRUCTURE",
                "INCREASE",
                claim_id="lease-liability",
                instrument_type="lease",
            )
        ],
        stage="growth",
    )

    assert pulls == ()


def test_type1_claim_bridge_rejects_unknown_assumption_filter_ids() -> None:
    with pytest.raises(ValueError, match="unknown assumption_ids: REVNEUE_CAGR"):
        build_type1_claim_assumption_pulls(
            [_claim("DEMAND_SIGNAL", "INCREASE", claim_id="demand-up")],
            stage="growth",
            assumption_ids=("REVNEUE_CAGR",),
        )


def test_type1_claim_bridge_deduplicates_to_strongest_economic_driver() -> None:
    weaker = _claim(
        "DEMAND_SIGNAL",
        "INCREASE",
        claim_id="weaker-revenue",
        magnitude_qualifier="WEAK",
        text="Revenue increased 10% year-over-year.",
    )
    stronger = _claim(
        "DEMAND_SIGNAL",
        "INCREASE",
        claim_id="stronger-revenue",
        magnitude_qualifier="EXTREME",
        text="Revenue increased 10% year-over-year.",
    )

    pulls = build_type1_claim_assumption_pulls(
        [weaker, stronger],
        stage="growth",
        assumption_ids=("REVENUE_CAGR",),
    )

    assert tuple(pull.claim_id for pull in pulls) == ("stronger-revenue",)


def test_type1_claim_bridge_is_deterministic_and_feeds_type1_axis_generation() -> None:
    claims = [
        _claim(
            "DEMAND_SIGNAL",
            "INCREASE",
            claim_id="demand-up-1",
            magnitude_qualifier="EXTREME",
        ),
        _claim(
            "DEMAND_SIGNAL",
            "INCREASE",
            claim_id="demand-up-2",
            magnitude_qualifier="STRONG",
            text="Sales backlog increased across data center products.",
        ),
        _claim(
            "DEMAND_SIGNAL",
            "DECREASE",
            claim_id="demand-down-1",
            magnitude_qualifier="EXTREME",
        ),
        _claim(
            "DEMAND_SIGNAL",
            "DECREASE",
            claim_id="demand-down-2",
            magnitude_qualifier="STRONG",
            text="Sales backlog declined across data center products.",
        ),
    ]

    first = build_type1_claim_assumption_pulls(
        claims,
        stage="growth",
        assumption_ids=("REVENUE_CAGR", "OPERATING_MARGIN"),
    )
    second = build_type1_claim_assumption_pulls(
        claims,
        stage="growth",
        assumption_ids=("REVENUE_CAGR", "OPERATING_MARGIN"),
    )
    axes = generate_type1_tension_axes(first, contested_mass_threshold=0.01)

    assert first == second
    assert len(axes) == 1
    assert axes[0].loadings["REVENUE_CAGR"] > 0.0
    assert axes[0].loadings["OPERATING_MARGIN"] > 0.0


def test_generate_narrative_axes_rejects_empty_claim_assumption_pulls() -> None:
    with pytest.raises(ValueError, match="claim-assumption pulls must be non-empty"):
        generate_narrative_axes(())


def test_unanimous_same_direction_claim_pulls_do_not_produce_type1_axis() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="claim-1", assumption_id="revenue_cagr", pull=0.80),
        ClaimAssumptionPull(claim_id="claim-2", assumption_id="revenue_cagr", pull=0.60),
        ClaimAssumptionPull(claim_id="claim-3", assumption_id="operating_margin", pull=0.50),
        ClaimAssumptionPull(claim_id="claim-4", assumption_id="operating_margin", pull=0.40),
    )

    axes = generate_type1_tension_axes(pulls, contested_mass_threshold=0.50)

    assert axes == ()


def test_assumption_stage_a_gate_requires_positive_and_negative_weighted_mass() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="growth-up", assumption_id="growth", pull=0.80),
        ClaimAssumptionPull(claim_id="growth-down", assumption_id="growth", pull=-0.70),
        ClaimAssumptionPull(claim_id="margin-up", assumption_id="margin", pull=0.90),
        ClaimAssumptionPull(claim_id="margin-down", assumption_id="margin", pull=-0.49),
        ClaimAssumptionPull(claim_id="wacc-up", assumption_id="wacc", pull=0.49),
        ClaimAssumptionPull(claim_id="wacc-down", assumption_id="wacc", pull=-0.90),
    )

    results = evaluate_type1_assumption_mass_gates(
        pulls,
        contested_mass_threshold=0.50,
    )

    assert {result.assumption_id: result.passes for result in results} == {
        "growth": True,
        "margin": False,
        "wacc": False,
    }
    growth = next(result for result in results if result.assumption_id == "growth")
    assert growth.positive_mass == pytest.approx(0.80)
    assert growth.negative_mass == pytest.approx(0.70)


def test_type1_diagnostics_preserve_stage_a_mass_gate_results() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="growth-up", assumption_id="growth", pull=0.80),
        ClaimAssumptionPull(claim_id="growth-down", assumption_id="growth", pull=-0.70),
        ClaimAssumptionPull(claim_id="margin-up", assumption_id="margin", pull=0.90),
        ClaimAssumptionPull(claim_id="margin-down", assumption_id="margin", pull=-0.49),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=0.50,
    )

    assert diagnostics.assumption_mass_gates == (
        evaluate_type1_assumption_mass_gates(
            pulls,
            contested_mass_threshold=0.50,
        )
    )
    assert {gate.assumption_id: gate.passes for gate in diagnostics.assumption_mass_gates} == {
        "growth": True,
        "margin": False,
    }


def test_centered_claim_by_assumption_matrix_recovers_bipolar_dominant_axis() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="revenue_cagr", pull=2.0),
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="wacc", pull=-1.0),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="margin", pull=0.5),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="wacc", pull=-0.5),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="revenue_cagr", pull=-1.0),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="margin", pull=-0.5),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="wacc", pull=0.5),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="revenue_cagr", pull=-2.0),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="margin", pull=-1.0),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="wacc", pull=1.0),
    )

    axes = generate_type1_tension_axes(pulls, contested_mass_threshold=1.0)

    assert len(axes) == 1
    assert axes[0].explained_variance_ratio == pytest.approx(1.0)
    expected_scale = np.sqrt(6.0)
    assert axes[0].loadings == pytest.approx(
        {
            "margin": 1.0 / expected_scale,
            "revenue_cagr": 2.0 / expected_scale,
            "wacc": -1.0 / expected_scale,
        }
    )


def test_type1_diagnostics_record_pca_scree_for_promoted_components() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="revenue_cagr", pull=2.0),
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="wacc", pull=-1.0),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="margin", pull=0.5),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="wacc", pull=-0.5),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="revenue_cagr", pull=-1.0),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="margin", pull=-0.5),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="wacc", pull=0.5),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="revenue_cagr", pull=-2.0),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="margin", pull=-1.0),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="wacc", pull=1.0),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=1.0,
    )

    assert len(diagnostics.candidate_components) == 1
    candidate = diagnostics.candidate_components[0]
    assert candidate.explained_variance_ratio == pytest.approx(1.0)
    assert candidate.cumulative_explained_variance_ratio == pytest.approx(1.0)
    assert candidate.rejection_reason is None
    assert candidate.promoted_axis == diagnostics.promoted_axes[0]


def test_generate_narrative_axes_uses_claim_assumption_type1_entrypoint() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="optimistic", assumption_id="revenue_cagr", pull=2.0),
        ClaimAssumptionPull(claim_id="optimistic", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="pessimistic", assumption_id="revenue_cagr", pull=-2.0),
        ClaimAssumptionPull(claim_id="pessimistic", assumption_id="margin", pull=-1.0),
    )

    axes = generate_narrative_axes(
        pulls,
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )

    assert axes == generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )
    expected_scale = np.sqrt(5.0)
    assert axes[0].loadings == pytest.approx(
        {
            "margin": 1.0 / expected_scale,
            "revenue_cagr": 2.0 / expected_scale,
        }
    )


def test_stage_c_rejects_axis_when_one_score_side_lacks_weighted_claim_mass() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="majority-1", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(claim_id="majority-2", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(
            claim_id="thin-outlier",
            assumption_id="revenue_cagr",
            pull=-20.0,
            weight=0.10,
        ),
    )

    axes = generate_type1_tension_axes(pulls, contested_mass_threshold=0.50)

    assert axes == ()


def test_type1_diagnostics_record_pca_scree_for_rejected_components() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="majority-1", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(claim_id="majority-2", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(
            claim_id="thin-outlier",
            assumption_id="revenue_cagr",
            pull=-20.0,
            weight=0.10,
        ),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=0.50,
        stability_threshold=0.0,
    )

    assert diagnostics.promoted_axes == ()
    assert len(diagnostics.candidate_components) == 1
    candidate = diagnostics.candidate_components[0]
    assert candidate.explained_variance_ratio == pytest.approx(1.0)
    assert candidate.cumulative_explained_variance_ratio == pytest.approx(1.0)
    assert candidate.rejection_reason == "stage_c_bipolar_mass_below_threshold"


def test_conditional_pull_enters_matrix_at_discounted_value() -> None:
    pulls = (
        ClaimAssumptionPull(
            claim_id="conditional-up",
            assumption_id="margin",
            pull=1.0,
            is_conditional=True,
        ),
        ClaimAssumptionPull(claim_id="realized-down", assumption_id="margin", pull=-0.5),
    )

    matrix, claim_ids, assumption_ids = _centered_claim_assumption_matrix(pulls, ("margin",))

    assert claim_ids == ("conditional-up", "realized-down")
    assert assumption_ids == ("margin",)
    assert matrix[0, 0] == pytest.approx(0.5)
    assert matrix[1, 0] == pytest.approx(-0.5)


def test_unconditional_pull_enters_matrix_at_face_value() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="realized-up", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="realized-down", assumption_id="margin", pull=-1.0),
    )

    matrix, claim_ids, assumption_ids = _centered_claim_assumption_matrix(pulls, ("margin",))

    assert claim_ids == ("realized-down", "realized-up")
    assert assumption_ids == ("margin",)
    assert matrix[1, 0] == pytest.approx(1.0)
    assert matrix[0, 0] == pytest.approx(-1.0)


def test_positional_claim_assumption_pull_preserves_lifecycle_and_is_unconditional() -> None:
    pull = ClaimAssumptionPull("c", "a", 1.0, 1.0, "mature", {"x": 1})

    assert pull.is_conditional is False
    assert pull.lifecycle_stage == "mature"
    assert pull.tam_structure == {"x": 1}
    assert _weighted_claim_assumption_pull(pull) == pytest.approx(1.0)


def test_mixed_conditional_set_produces_correctly_discounted_matrix() -> None:
    pulls = (
        ClaimAssumptionPull(
            claim_id="conditional",
            assumption_id="margin",
            pull=1.0,
            is_conditional=True,
        ),
        ClaimAssumptionPull(claim_id="realized", assumption_id="margin", pull=1.0),
    )

    matrix, claim_ids, assumption_ids = _centered_claim_assumption_matrix(pulls, ("margin",))

    assert claim_ids == ("conditional", "realized")
    assert assumption_ids == ("margin",)
    assert matrix[0, 0] == pytest.approx(-0.25)
    assert matrix[1, 0] == pytest.approx(0.25)


def test_stable_axis_passes_leave_one_out_gate() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="positive-1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="positive-2", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="positive-3", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="negative-1", assumption_id="margin", pull=-1.0),
        ClaimAssumptionPull(claim_id="negative-2", assumption_id="margin", pull=-1.0),
    )

    axes = generate_type1_tension_axes(pulls, contested_mass_threshold=1.0)

    assert len(axes) == 1
    assert axes[0].loadings == pytest.approx({"margin": 1.0})


def test_stability_gate_preserves_secondary_orthogonal_type1_axis() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="axis-1-positive", assumption_id="growth", pull=3.0),
        ClaimAssumptionPull(claim_id="axis-1-negative", assumption_id="growth", pull=-3.0),
        ClaimAssumptionPull(claim_id="axis-2-positive", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="axis-2-negative", assumption_id="margin", pull=-1.0),
    )

    axes = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
        explained_variance_threshold=1.0,
    )

    assert len(axes) == 2


def test_type1_diagnostics_expose_axis_budget_rejection_reason() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="axis-1-positive", assumption_id="growth", pull=3.0),
        ClaimAssumptionPull(claim_id="axis-1-negative", assumption_id="growth", pull=-3.0),
        ClaimAssumptionPull(claim_id="axis-2-positive", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="axis-2-negative", assumption_id="margin", pull=-1.0),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=1.0,
        explained_variance_threshold=1.0,
        max_axes=1,
    )

    assert len(diagnostics.promoted_axes) == 1
    assert len(diagnostics.candidate_components) == 2
    promoted_candidate = diagnostics.candidate_components[0]
    rejected_candidate = diagnostics.candidate_components[1]
    assert promoted_candidate.rejection_reason is None
    assert promoted_candidate.promoted_axis is not None
    assert rejected_candidate.rejection_reason == "axis_budget_already_filled"
    assert rejected_candidate.promoted_axis is None


def test_unstable_axis_is_rejected_by_stability_gate() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="positive", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="negative", assumption_id="margin", pull=-1.0),
    )

    axes = generate_type1_tension_axes(pulls, contested_mass_threshold=1.0)

    assert axes == ()


def test_type1_diagnostics_expose_stability_rejection_reason() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="positive", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="negative", assumption_id="margin", pull=-1.0),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=1.0,
    )

    assert diagnostics.promoted_axes == ()
    assert len(diagnostics.candidate_components) == 1
    candidate = diagnostics.candidate_components[0]
    assert candidate.stability_score == pytest.approx(0.0)
    assert candidate.rejection_reason == "stability_below_threshold"


def test_type1_diagnostics_expose_stage_c_mass_rejection_reason() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="majority-1", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(claim_id="majority-2", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(
            claim_id="thin-outlier",
            assumption_id="revenue_cagr",
            pull=-20.0,
            weight=0.10,
        ),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=0.50,
        stability_threshold=0.0,
    )

    candidate = diagnostics.candidate_components[0]
    assert candidate.rejection_reason == "stage_c_bipolar_mass_below_threshold"
    assert candidate.stage_c_mass_gate.positive_mass == pytest.approx(2.0)
    assert candidate.stage_c_mass_gate.negative_mass == pytest.approx(0.10)
    assert candidate.stage_c_mass_gate.passes is False


def test_type1_diagnostics_promoted_axes_match_existing_api_output() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="positive-1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="positive-2", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="positive-3", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="negative-1", assumption_id="margin", pull=-1.0),
        ClaimAssumptionPull(claim_id="negative-2", assumption_id="margin", pull=-1.0),
    )

    diagnostics = generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=1.0,
    )

    assert diagnostics.promoted_axes == generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
    )


def test_outlier_supported_axis_fails_stability_when_outlier_removed() -> None:
    # The growth axis exists only because of the single "growth-outlier" claim
    # (growth pull 2.0). Removing that claim collapses the growth column variance
    # to zero, so the growth axis disappears in that leave-one-out fold while the
    # orthogonal margin axis keeps varying. The outlier-driven axis must be rejected
    # by the stability gate; only the genuinely stable margin axis may survive.
    # (Regression for the cycle-3 P1: the LOO null-space direction still has a
    # unit-norm right singular vector, so filtering folds by vector norm — instead
    # of by singular value — would let the collapsed axis match the null space and
    # be promoted.)
    pulls = (
        ClaimAssumptionPull(claim_id="growth-outlier", assumption_id="growth", pull=2.0),
        ClaimAssumptionPull(claim_id="claim-b", assumption_id="growth", pull=-1.0),
        ClaimAssumptionPull(claim_id="claim-c", assumption_id="growth", pull=-1.0),
        ClaimAssumptionPull(claim_id="claim-b", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="claim-c", assumption_id="margin", pull=-1.0),
    )

    axes = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
        explained_variance_threshold=1.0,
    )

    # The outlier-driven growth axis must not be promoted: once "growth-outlier" is
    # removed the two remaining growth pulls are equal, so the growth direction has
    # zero variance in that fold and matches only the LOO null space. No returned
    # axis may be growth-dominated. (The buggy vector-norm filter returned a
    # growth axis with loadings {"growth": 1.0}.) Survival of genuinely stable axes
    # is covered by test_stable_axis_passes_leave_one_out_gate and
    # test_stability_gate_preserves_secondary_orthogonal_type1_axis.
    assert all(
        abs(axis.loadings["growth"]) < abs(axis.loadings["margin"]) for axis in axes
    )


@pytest.mark.parametrize(
    ("matrix", "axis_loadings"),
    [
        (np.array([[1.0], [-1.0]], dtype=np.float64), np.array([1.0], dtype=np.float64)),
        (
            np.array([[1.0], [1.0], [-1.0]], dtype=np.float64),
            np.array([1.0], dtype=np.float64),
        ),
        (
            np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, -1.0]], dtype=np.float64),
            np.array([1.0, 0.0], dtype=np.float64),
        ),
    ],
)
def test_stability_score_is_bounded_zero_to_one(
    matrix: np.ndarray[tuple[int, int], np.dtype[np.float64]],
    axis_loadings: np.ndarray[tuple[int], np.dtype[np.float64]],
) -> None:
    score = _axis_stability_score(matrix, axis_loadings)

    assert 0.0 <= score <= 1.0


def test_builds_signed_pull_signature_from_contested_evidence() -> None:
    signature = build_pull_signature(
        ContestedAssumptionPullInput(
            assumption_id="revenue_cagr",
            supporting=(
                EvidencePull(claim_id="support-1", values=(0.30, 0.10)),
                EvidencePull(claim_id="support-2", values=(0.20, 0.40)),
            ),
            contradicting=(
                EvidencePull(claim_id="risk-1", values=(0.10, 0.05)),
                EvidencePull(claim_id="risk-2", values=(0.03, 0.15)),
            ),
        )
    )

    assert signature.assumption_id == "revenue_cagr"
    assert signature.lifecycle_stage == "growth"
    assert signature.tam_structure == {}
    assert signature.values == pytest.approx((0.37, 0.30))


def test_rejects_duplicate_contested_evidence_claim_ids_before_aggregation() -> None:
    contested = ContestedAssumptionPullInput(
        assumption_id="revenue_cagr",
        supporting=(EvidencePull(claim_id="claim-1", values=(0.30,)),),
        contradicting=(EvidencePull(claim_id="claim-1", values=(0.10,)),),
    )

    with pytest.raises(ValueError, match="duplicate claim ids"):
        build_pull_signature(contested)


def test_contested_pull_signature_preserves_measurement_axis_metadata() -> None:
    tam_structure = {"market": "ai-accelerators", "segments": ("training", "inference")}

    signature = build_pull_signature(
        ContestedAssumptionPullInput(
            assumption_id="tam",
            supporting=(EvidencePull(claim_id="support", values=(100.0,)),),
            contradicting=(EvidencePull(claim_id="risk", values=(25.0,)),),
            lifecycle_stage="mature",
            tam_structure=tam_structure,
        )
    )

    assert signature.lifecycle_stage == "mature"
    assert signature.tam_structure == tam_structure
    assert signature.values == pytest.approx((75.0,))


def test_legacy_pull_signatures_are_rejected_as_type1_axis_entrypoint() -> None:
    signatures = (
        build_pull_signature(
            ContestedAssumptionPullInput(
                assumption_id="revenue_cagr",
                supporting=(EvidencePull(claim_id="revenue-support", values=(3.0, 0.0)),),
                contradicting=(),
            )
        ),
        build_pull_signature(
            ContestedAssumptionPullInput(
                assumption_id="wacc",
                supporting=(),
                contradicting=(EvidencePull(claim_id="wacc-risk", values=(1.0, 0.0)),),
            )
        ),
    )

    with pytest.raises(ValueError, match="ClaimAssumptionPull"):
        generate_narrative_axes(signatures)


def test_rejects_empty_contested_evidence() -> None:
    contested = ContestedAssumptionPullInput(
        assumption_id="revenue_cagr",
        supporting=(),
        contradicting=(),
    )

    with pytest.raises(ValueError, match="evidence"):
        build_pull_signature(contested)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_contested_evidence_values(bad_value: float) -> None:
    contested = ContestedAssumptionPullInput(
        assumption_id="revenue_cagr",
        supporting=(EvidencePull(claim_id="support", values=(0.30, bad_value)),),
        contradicting=(),
    )

    with pytest.raises(ValueError, match="finite"):
        build_pull_signature(contested)


def test_rejects_empty_contested_assumption_id() -> None:
    contested = ContestedAssumptionPullInput(
        assumption_id="",
        supporting=(EvidencePull(claim_id="support", values=(0.30,)),),
        contradicting=(),
    )

    with pytest.raises(ValueError, match="assumption_id"):
        build_pull_signature(contested)


def test_rejects_malformed_contested_evidence_vectors() -> None:
    contested = ContestedAssumptionPullInput(
        assumption_id="revenue_cagr",
        supporting=(EvidencePull(claim_id="support", values=np.array([[0.30]])),),
        contradicting=(),
    )

    with pytest.raises(ValueError, match="one-dimensional"):
        build_pull_signature(contested)


def test_rejects_duplicate_claim_assumption_cells() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="claim-1", assumption_id="revenue_cagr", pull=0.20),
        ClaimAssumptionPull(claim_id="claim-1", assumption_id="revenue_cagr", pull=-0.10),
    )

    with pytest.raises(ValueError, match="unique per claim and assumption"):
        generate_narrative_axes(pulls)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nan_or_infinite_claim_assumption_pulls(bad_value: float) -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="claim-1", assumption_id="revenue_cagr", pull=bad_value),
        ClaimAssumptionPull(claim_id="claim-2", assumption_id="revenue_cagr", pull=-0.40),
    )

    with pytest.raises(ValueError, match="finite"):
        generate_narrative_axes(pulls)


def test_legacy_pull_signatures_cannot_promote_unanimous_consensus_to_axis() -> None:
    signatures = (
        PullSignature(assumption_id="revenue_cagr", values=(3.0, 0.0)),
        PullSignature(assumption_id="operating_margin", values=(2.0, 0.0)),
        PullSignature(assumption_id="tam", values=(1.0, 0.0)),
    )

    with pytest.raises(ValueError, match="aggregated PullSignature"):
        generate_narrative_axes(signatures)


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
    assert positive["REVENUE_CAGR"].base_mu == pytest.approx(0.14)
    assert negative["REVENUE_CAGR"].current_mu == pytest.approx(0.06)
    assert negative["REVENUE_CAGR"].base_mu == pytest.approx(0.06)
    assert positive["OPERATING_MARGIN"].current_mu == pytest.approx(0.16)
    assert positive["OPERATING_MARGIN"].base_mu == pytest.approx(0.16)
    assert negative["OPERATING_MARGIN"].current_mu == pytest.approx(0.24)
    assert negative["OPERATING_MARGIN"].base_mu == pytest.approx(0.24)
    assert positive["WACC"].current_mu == pytest.approx(0.09)
    assert positive["WACC"].base_mu == pytest.approx(0.09)
    assert negative["WACC"].current_mu == pytest.approx(0.09)
    assert negative["WACC"].base_mu == pytest.approx(0.09)


def test_type1_candidate_shift_reaches_resolved_mu_path() -> None:
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"TAM": 1.0},
    )

    positive, negative = generate_type1_narrative_candidates(
        axis,
        assumptions=(_assumption("TAM", 1_000_000_000.0, shift_scale=100_000_000.0),),
    )
    positive_assumption = positive.active_assumptions[0]
    negative_assumption = negative.active_assumptions[0]

    positive_mu = resolved_mu(
        positive_assumption,
        {},
        company={},
        t_year=0.0,
    )
    negative_mu = resolved_mu(
        negative_assumption,
        {},
        company={},
        t_year=0.0,
    )

    assert positive_mu == pytest.approx(1_100_000_000.0)
    assert negative_mu == pytest.approx(900_000_000.0)
    assert positive_mu != negative_mu


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


def _claim(
    subject: ClaimSubject,
    direction: ClaimDirection,
    *,
    claim_id: str,
    magnitude_qualifier: MagnitudeQualifier = "STRONG",
    text: str = "NVDA narrative claim.",
    instrument_type: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=text,
        claim_subject=subject,
        claim_nature="REALIZED",
        direction=direction,
        magnitude_qualifier=magnitude_qualifier,
        instrument_type=instrument_type,
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.95,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=SourceRef(
            discovery_channel="rss_aggregator",
            content_source="10-Q",
            source_reliability=0.95,
        ),
        chunk_ref="chunk",
        published_date=date(2026, 5, 22),
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
