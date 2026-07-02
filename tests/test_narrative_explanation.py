from __future__ import annotations

from datetime import date

import pytest

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.claim import Claim, ExtractionQuality, SourceRef
from dcf_engine.distributions import DistributionFamily
from dcf_engine.narrative_axes import (
    ClaimAssumptionPull,
    NarrativeAxis,
    generate_type1_narrative_candidates,
    generate_type1_tension_axes,
)
from dcf_engine.narrative_explanation import build_type1_fact_explanations


def test_shared_fact_anchor_with_bull_and_bear_pulls_creates_type1_explanation_pair() -> None:
    claims = (
        _claim("bear", text="Data center revenue increased 217% year over year."),
        _claim("bull", text="Data center revenue increased 217% year over year."),
    )
    pulls = (
        ClaimAssumptionPull(claim_id="bull", assumption_id="REVENUE_CAGR", pull=1.0),
        ClaimAssumptionPull(claim_id="bear", assumption_id="REVENUE_CAGR", pull=-0.8),
    )
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"REVENUE_CAGR": 1.0},
    )

    explanations = build_type1_fact_explanations(
        claims=claims,
        pulls=pulls,
        axes=(axis,),
    )

    assert len(explanations) == 1
    row = explanations[0]
    assert row.axis_index == 0
    assert row.assumption_id == "REVENUE_CAGR"
    assert row.fact_anchor.claim_text == "Data center revenue increased 217% year over year."
    assert row.positive_evidence.claim_id == "bull"
    assert row.positive_evidence.pull == pytest.approx(1.0)
    assert row.negative_evidence.claim_id == "bear"
    assert row.negative_evidence.pull == pytest.approx(-0.8)


def test_missing_unmatched_or_one_sided_fact_anchors_do_not_create_pairs() -> None:
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"REVENUE_CAGR": 1.0},
    )

    assert (
        build_type1_fact_explanations(
            claims=(
                _claim("missing-positive", text=" "),
                _claim("missing-negative", text=" "),
            ),
            pulls=(
                ClaimAssumptionPull(
                    claim_id="missing-positive",
                    assumption_id="REVENUE_CAGR",
                    pull=1.0,
                ),
                ClaimAssumptionPull(
                    claim_id="missing-negative",
                    assumption_id="REVENUE_CAGR",
                    pull=-1.0,
                ),
            ),
            axes=(axis,),
        )
        == ()
    )
    assert (
        build_type1_fact_explanations(
            claims=(
                _claim("bull", text="Data center revenue increased 217%."),
                _claim("bear", text="Gross margin narrowed 260 basis points."),
            ),
            pulls=(
                ClaimAssumptionPull(claim_id="bull", assumption_id="REVENUE_CAGR", pull=1.0),
                ClaimAssumptionPull(claim_id="bear", assumption_id="REVENUE_CAGR", pull=-1.0),
            ),
            axes=(axis,),
        )
        == ()
    )
    assert (
        build_type1_fact_explanations(
            claims=(
                _claim("bull-1", text="Data center revenue increased 217%."),
                _claim("bull-2", text="Data center revenue increased 217%."),
            ),
            pulls=(
                ClaimAssumptionPull(claim_id="bull-1", assumption_id="REVENUE_CAGR", pull=1.0),
                ClaimAssumptionPull(claim_id="bull-2", assumption_id="REVENUE_CAGR", pull=0.5),
            ),
            axes=(axis,),
        )
        == ()
    )


def test_explanation_generation_does_not_change_type1_axis_or_candidate_behavior() -> None:
    pulls = (
        ClaimAssumptionPull(claim_id="bull", assumption_id="REVENUE_CAGR", pull=1.0),
        ClaimAssumptionPull(claim_id="bear", assumption_id="REVENUE_CAGR", pull=-1.0),
        ClaimAssumptionPull(claim_id="confirming", assumption_id="REVENUE_CAGR", pull=0.5),
    )
    axes_before = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=0.5,
        stability_threshold=0.0,
    )
    candidates_before = generate_type1_narrative_candidates(
        axes_before[0],
        assumptions=(_assumption("REVENUE_CAGR", 0.10),),
    )

    build_type1_fact_explanations(
        claims=(
            _claim("bull", text="Data center revenue increased 217%."),
            _claim("bear", text="Data center revenue increased 217%."),
            _claim("confirming", text="Gross margin widened."),
        ),
        pulls=pulls,
        axes=axes_before,
    )

    axes_after = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=0.5,
        stability_threshold=0.0,
    )
    candidates_after = generate_type1_narrative_candidates(
        axes_after[0],
        assumptions=(_assumption("REVENUE_CAGR", 0.10),),
    )

    assert axes_after == axes_before
    assert candidates_after == candidates_before


def test_explanation_records_preserve_deterministic_ordering_and_provenance() -> None:
    source_ref = SourceRef(
        discovery_channel="rss_aggregator",
        content_source="10-Q",
        source_reliability=0.95,
    )
    claims = (
        _claim("negative", text="Data center revenue increased 217%.", source_ref=source_ref),
        _claim("positive", text="Data center revenue increased 217%.", source_ref=source_ref),
    )
    pulls = (
        ClaimAssumptionPull(claim_id="positive", assumption_id="REVENUE_CAGR", pull=1.0),
        ClaimAssumptionPull(claim_id="negative", assumption_id="REVENUE_CAGR", pull=-1.0),
    )
    axis = NarrativeAxis(
        axis_index=2,
        explained_variance_ratio=0.82,
        loadings={"REVENUE_CAGR": 1.0},
    )

    forward = build_type1_fact_explanations(claims=claims, pulls=pulls, axes=(axis,))
    reversed_rows = build_type1_fact_explanations(
        claims=tuple(reversed(claims)),
        pulls=tuple(reversed(pulls)),
        axes=(axis,),
    )

    assert forward == reversed_rows
    row = forward[0]
    assert (row.positive_evidence.claim_id, row.negative_evidence.claim_id) == (
        "positive",
        "negative",
    )
    assert row.positive_evidence.chunk_ref == "chunk"
    assert row.positive_evidence.published_date == date(2026, 5, 22)
    assert row.positive_evidence.source_ref == source_ref
    assert row.negative_evidence.chunk_ref == "chunk"
    assert row.negative_evidence.published_date == date(2026, 5, 22)
    assert row.negative_evidence.source_ref == source_ref
    assert row.assumption_id == "REVENUE_CAGR"


@pytest.mark.parametrize("bad_pull", [float("nan"), float("inf"), float("-inf")])
def test_explanation_rejects_non_finite_claim_assumption_pulls(bad_pull: float) -> None:
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=1.0,
        loadings={"REVENUE_CAGR": 1.0},
    )

    with pytest.raises(ValueError, match="finite"):
        build_type1_fact_explanations(
            claims=(_claim("bad", text="Data center revenue increased 217%."),),
            pulls=(
                ClaimAssumptionPull(
                    claim_id="bad",
                    assumption_id="REVENUE_CAGR",
                    pull=bad_pull,
                ),
            ),
            axes=(axis,),
        )


def test_multi_loading_axis_explanations_ignore_unrelated_assumptions() -> None:
    claims = (
        _claim("bull", text="Data center revenue increased 217%."),
        _claim("bear", text="Data center revenue increased 217%."),
    )
    pulls = (
        ClaimAssumptionPull(claim_id="bull", assumption_id="REVENUE_CAGR", pull=1.0),
        ClaimAssumptionPull(claim_id="bear", assumption_id="REVENUE_CAGR", pull=-1.0),
        ClaimAssumptionPull(claim_id="bull", assumption_id="OPERATING_MARGIN", pull=0.5),
        ClaimAssumptionPull(claim_id="bear", assumption_id="OPERATING_MARGIN", pull=-0.5),
        ClaimAssumptionPull(claim_id="bull", assumption_id="WACC", pull=-0.5),
        ClaimAssumptionPull(claim_id="bear", assumption_id="WACC", pull=0.5),
    )
    axis = NarrativeAxis(
        axis_index=0,
        explained_variance_ratio=0.90,
        loadings={"OPERATING_MARGIN": -0.25, "REVENUE_CAGR": 0.75},
    )

    explanations = build_type1_fact_explanations(claims=claims, pulls=pulls, axes=(axis,))

    assert tuple(row.assumption_id for row in explanations) == (
        "OPERATING_MARGIN",
        "REVENUE_CAGR",
    )
    assert {row.assumption_id for row in explanations} == set(axis.loadings)


def test_generated_orthogonal_axes_explain_only_material_loading_assumptions() -> None:
    claims = (
        _claim("c1", text="The same fact is interpreted in opposing ways."),
        _claim("c2", text="The same fact is interpreted in opposing ways."),
        _claim("c3", text="The same fact is interpreted in opposing ways."),
        _claim("c4", text="The same fact is interpreted in opposing ways."),
    )
    pulls = (
        ClaimAssumptionPull(claim_id="c1", assumption_id="growth", pull=2.0),
        ClaimAssumptionPull(claim_id="c1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="c2", assumption_id="growth", pull=2.0),
        ClaimAssumptionPull(claim_id="c2", assumption_id="margin", pull=-1.0),
        ClaimAssumptionPull(claim_id="c3", assumption_id="growth", pull=-2.0),
        ClaimAssumptionPull(claim_id="c3", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="c4", assumption_id="growth", pull=-2.0),
        ClaimAssumptionPull(claim_id="c4", assumption_id="margin", pull=-1.0),
    )
    axes = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
        explained_variance_threshold=1.0,
        stability_threshold=0.0,
        max_axes=2,
    )

    assert axes[0].loadings == pytest.approx({"growth": 1.0, "margin": 0.0})
    assert axes[1].loadings == pytest.approx({"growth": 0.0, "margin": 1.0})

    explanations = build_type1_fact_explanations(claims=claims, pulls=pulls, axes=axes)

    assert {(row.axis_index, row.assumption_id) for row in explanations} == {
        (0, "growth"),
        (1, "margin"),
    }


def _claim(
    claim_id: str,
    *,
    text: str,
    source_ref: SourceRef | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=text,
        claim_subject="DEMAND_SIGNAL",
        claim_nature="REALIZED",
        direction="INCREASE",
        magnitude_qualifier="MODERATE",
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.95,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=source_ref
        or SourceRef(
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
    family: DistributionFamily = "normal",
) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=mu,
        current_sigma=0.01,
        base_mu=mu,
        base_sigma=0.01,
        shift_scale=ScaleSpec(center=0.05, uncertainty=0.0),
        constraints={"low": 0.0, "high": 1.0},
        active=True,
    )
