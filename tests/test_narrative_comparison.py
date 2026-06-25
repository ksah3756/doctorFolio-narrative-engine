import pytest

from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.narrative import Narrative, NarrativeContainer, NarrativeScenarioSet
from dcf_engine.narrative_comparison import build_narrative_comparison


def test_comparison_rejects_metric_map_narrative_ids_that_do_not_match() -> None:
    containers = [
        _container("base"),
        _container("platform"),
    ]

    with pytest.raises(ValueError, match="narrative ids"):
        build_narrative_comparison(
            containers=containers,
            metrics_by_narrative={
                "base": {"REVENUE_CAGR": 0.08},
                "supplier": {"REVENUE_CAGR": 0.06},
            },
        )


def test_comparison_uses_stable_metric_rows_and_narrative_columns() -> None:
    comparison = build_narrative_comparison(
        containers=[
            _container("platform"),
            _container("base"),
        ],
        metrics_by_narrative={
            "platform": {
                "OPERATING_MARGIN": 0.24,
                "REVENUE_CAGR": 0.12,
            },
            "base": {
                "REVENUE_CAGR": 0.08,
                "OPERATING_MARGIN": 0.20,
            },
        },
    )

    assert [column.narrative_id for column in comparison.narratives] == [
        "base",
        "platform",
    ]
    assert [row.metric_id for row in comparison.rows] == [
        "OPERATING_MARGIN",
        "REVENUE_CAGR",
    ]
    assert comparison.rows[0].values == (0.20, 0.24)
    assert comparison.rows[1].values == (0.08, 0.12)


@pytest.mark.parametrize(
    "metrics_by_narrative",
    [
        {
            "base": {"REVENUE_CAGR": 0.08},
            "platform": {},
        },
        {
            "base": {"REVENUE_CAGR": 0.08},
            "platform": {"REVENUE_CAGR": float("nan")},
        },
        {
            "base": {"REVENUE_CAGR": 0.08},
            "platform": {"REVENUE_CAGR": float("inf")},
        },
    ],
)
def test_comparison_rejects_missing_nan_or_infinite_metric_values(
    metrics_by_narrative: dict[str, dict[str, float]],
) -> None:
    with pytest.raises(ValueError, match="metric"):
        build_narrative_comparison(
            containers=[_container("base"), _container("platform")],
            metrics_by_narrative=metrics_by_narrative,
        )


def test_comparison_preserves_type_2_metadata_per_narrative() -> None:
    comparison = build_narrative_comparison(
        containers=[
            _container(
                "platform",
                lifecycle_stage="growth",
                tam_structure={"market": "software", "scope": "platform"},
            ),
            _container(
                "supplier",
                lifecycle_stage="mature",
                tam_structure={"market": "hardware", "scope": "supplier"},
            ),
        ],
        metrics_by_narrative={
            "supplier": {"REVENUE_CAGR": 0.05},
            "platform": {"REVENUE_CAGR": 0.12},
        },
    )

    assert [
        (column.narrative_id, column.lifecycle_stage, column.tam_structure)
        for column in comparison.narratives
    ] == [
        (
            "platform",
            "growth",
            {"market": "software", "scope": "platform"},
        ),
        (
            "supplier",
            "mature",
            {"market": "hardware", "scope": "supplier"},
        ),
    ]


def test_type_2_comparison_is_display_only_not_probability_weighted() -> None:
    platform = _container(
        "platform",
        lifecycle_stage="growth",
        tam_structure={"market": "software"},
    )
    supplier = _container(
        "supplier",
        lifecycle_stage="mature",
        tam_structure={"market": "hardware"},
    )

    comparison = build_narrative_comparison(
        containers=[platform, supplier],
        metrics_by_narrative={
            "platform": {"REVENUE_CAGR": 0.12},
            "supplier": {"REVENUE_CAGR": 0.05},
        },
    )

    assert [column.narrative_id for column in comparison.narratives] == [
        "platform",
        "supplier",
    ]
    with pytest.raises(ValueError, match="measurement axis"):
        NarrativeScenarioSet.from_containers(
            containers=[platform, supplier],
            probabilities_by_narrative={"platform": 0.50, "supplier": 0.50},
        )


def _container(
    narrative_id: str,
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
        assumptions=(),
    )
