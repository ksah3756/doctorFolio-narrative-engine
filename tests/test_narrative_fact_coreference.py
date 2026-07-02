import pytest

from dcf_engine.narrative_fact_coreference import (
    FactCoreferenceClaimKind,
    FactCoreferenceEvidence,
    FactCoreferenceFactKey,
    build_fact_coreference_explanations,
)


def test_groups_opposing_interpretation_claims_that_share_one_fact_key() -> None:
    explanations = build_fact_coreference_explanations(
        (
            _evidence(
                "bullish-interpretation",
                evidence_span="Data center revenue increased 217% year over year.",
                assumption_pulls={"REVENUE_CAGR": 1.2},
            ),
            _evidence(
                "capacity-constrained-interpretation",
                evidence_span="Data center revenue increased 217% year over year.",
                assumption_pulls={"REVENUE_CAGR": -0.8},
            ),
        )
    )

    assert len(explanations) == 1
    row = explanations[0]
    assert row.fact_key == FactCoreferenceFactKey(
        source_id="FY2024-10K",
        evidence_span="Data center revenue increased 217% year over year.",
        metric_id="data_center_revenue_growth",
        period="FY2024",
    )
    assert row.claim_ids == (
        "bullish-interpretation",
        "capacity-constrained-interpretation",
    )
    assert row.opposing_assumptions[0].assumption_id == "REVENUE_CAGR"
    assert row.opposing_assumptions[0].positive_claim_ids == ("bullish-interpretation",)
    assert row.opposing_assumptions[0].negative_claim_ids == (
        "capacity-constrained-interpretation",
    )


def test_keeps_different_metric_period_source_or_span_in_separate_groups() -> None:
    explanations = build_fact_coreference_explanations(
        (
            _evidence("same-fact-up", assumption_pulls={"REVENUE_CAGR": 1.0}),
            _evidence("same-fact-down", assumption_pulls={"REVENUE_CAGR": -1.0}),
            _evidence(
                "different-metric-up",
                metric_id="total_revenue_growth",
                assumption_pulls={"REVENUE_CAGR": 1.0},
            ),
            _evidence(
                "different-metric-down",
                metric_id="total_revenue_growth",
                assumption_pulls={"REVENUE_CAGR": -1.0},
            ),
            _evidence(
                "different-period-up",
                period="Q1-FY2025",
                assumption_pulls={"REVENUE_CAGR": 1.0},
            ),
            _evidence(
                "different-period-down",
                period="Q1-FY2025",
                assumption_pulls={"REVENUE_CAGR": -1.0},
            ),
            _evidence(
                "different-source-up",
                source_id="Q1-FY2025-10Q",
                assumption_pulls={"REVENUE_CAGR": 1.0},
            ),
            _evidence(
                "different-source-down",
                source_id="Q1-FY2025-10Q",
                assumption_pulls={"REVENUE_CAGR": -1.0},
            ),
            _evidence(
                "different-span-up",
                evidence_span="Revenue grew 18% year over year.",
                assumption_pulls={"REVENUE_CAGR": 1.0},
            ),
            _evidence(
                "different-span-down",
                evidence_span="Revenue grew 18% year over year.",
                assumption_pulls={"REVENUE_CAGR": -1.0},
            ),
        )
    )

    assert len(explanations) == 5
    assert {row.fact_key for row in explanations} == {
        FactCoreferenceFactKey(
            source_id="FY2024-10K",
            evidence_span="Data center revenue increased 217% year over year.",
            metric_id="data_center_revenue_growth",
            period="FY2024",
        ),
        FactCoreferenceFactKey(
            source_id="FY2024-10K",
            evidence_span="Data center revenue increased 217% year over year.",
            metric_id="data_center_revenue_growth",
            period="Q1-FY2025",
        ),
        FactCoreferenceFactKey(
            source_id="FY2024-10K",
            evidence_span="Data center revenue increased 217% year over year.",
            metric_id="total_revenue_growth",
            period="FY2024",
        ),
        FactCoreferenceFactKey(
            source_id="FY2024-10K",
            evidence_span="Revenue grew 18% year over year.",
            metric_id="data_center_revenue_growth",
            period="FY2024",
        ),
        FactCoreferenceFactKey(
            source_id="Q1-FY2025-10Q",
            evidence_span="Data center revenue increased 217% year over year.",
            metric_id="data_center_revenue_growth",
            period="FY2024",
        ),
    }
    assert len({row.group_id for row in explanations}) == 5


def test_excludes_fact_and_shared_observation_claims_from_explanation_groups() -> None:
    explanations = build_fact_coreference_explanations(
        (
            _evidence(
                "reported-fact",
                claim_kind="FACT",
                assumption_pulls={"REVENUE_CAGR": 1.0},
            ),
            _evidence(
                "shared-observation",
                claim_kind="SHARED_OBSERVATION",
                assumption_pulls={"REVENUE_CAGR": -1.0},
            ),
            _evidence("interpretation", assumption_pulls={"REVENUE_CAGR": 1.0}),
            _evidence(
                "projection",
                claim_kind="PROJECTION",
                assumption_pulls={"REVENUE_CAGR": -1.0},
            ),
        )
    )

    assert len(explanations) == 1
    assert explanations[0].claim_ids == ("interpretation", "projection")


def test_preserves_deterministic_group_ids_and_output_order_when_inputs_reverse() -> None:
    evidence = (
        _evidence(
            "fact-b-up",
            metric_id="margin",
            assumption_pulls={"OPERATING_MARGIN": 1.0},
        ),
        _evidence(
            "fact-b-down",
            metric_id="margin",
            assumption_pulls={"OPERATING_MARGIN": -1.0},
        ),
        _evidence("fact-a-up", metric_id="growth", assumption_pulls={"REVENUE_CAGR": 1.0}),
        _evidence("fact-a-down", metric_id="growth", assumption_pulls={"REVENUE_CAGR": -1.0}),
    )

    forward = build_fact_coreference_explanations(evidence)
    reversed_rows = build_fact_coreference_explanations(tuple(reversed(evidence)))

    assert forward == reversed_rows
    assert [row.fact_key.metric_id for row in forward] == ["growth", "margin"]
    assert [row.group_id for row in forward] == [
        "fact-coref-4c2847413a26",
        "fact-coref-65d8997dd96f",
    ]


def test_rejects_empty_inputs_and_duplicate_claim_ids() -> None:
    with pytest.raises(ValueError, match="fact-coreference evidence must be non-empty"):
        build_fact_coreference_explanations(())

    with pytest.raises(ValueError, match="duplicate claim ids: duplicate"):
        build_fact_coreference_explanations(
            (
                _evidence("duplicate", assumption_pulls={"REVENUE_CAGR": 1.0}),
                _evidence("duplicate", assumption_pulls={"REVENUE_CAGR": -1.0}),
            )
        )


def _evidence(
    claim_id: str,
    *,
    claim_kind: FactCoreferenceClaimKind = "INTERPRETATION",
    source_id: str = "FY2024-10K",
    evidence_span: str = "Data center revenue increased 217% year over year.",
    metric_id: str = "data_center_revenue_growth",
    period: str = "FY2024",
    assumption_pulls: dict[str, float],
) -> FactCoreferenceEvidence:
    return FactCoreferenceEvidence(
        claim_id=claim_id,
        claim_kind=claim_kind,
        source_id=source_id,
        evidence_span=evidence_span,
        metric_id=metric_id,
        period=period,
        assumption_pulls=assumption_pulls,
    )
