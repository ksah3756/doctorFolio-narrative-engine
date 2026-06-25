"""Display-only comparison artifact for narrative metric values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite

from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.narrative import NarrativeContainer

type MetricMapByNarrative = Mapping[str, Mapping[str, float]]


@dataclass(frozen=True)
class NarrativeComparisonColumn:
    narrative_id: str
    lifecycle_stage: LifecycleStage
    tam_structure: dict[str, object]


@dataclass(frozen=True)
class NarrativeComparisonRow:
    metric_id: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class NarrativeComparison:
    narratives: tuple[NarrativeComparisonColumn, ...]
    rows: tuple[NarrativeComparisonRow, ...]


def build_narrative_comparison(
    *,
    containers: Iterable[NarrativeContainer],
    metrics_by_narrative: MetricMapByNarrative,
) -> NarrativeComparison:
    containers_by_narrative = _containers_by_narrative_id(containers)
    narrative_ids = tuple(sorted(containers_by_narrative))
    _validate_narrative_ids(narrative_ids, metrics_by_narrative)
    metric_ids = _metric_ids(metrics_by_narrative, narrative_ids)

    return NarrativeComparison(
        narratives=tuple(
            NarrativeComparisonColumn(
                narrative_id=narrative_id,
                lifecycle_stage=containers_by_narrative[narrative_id].narrative.lifecycle_stage,
                tam_structure=dict(
                    containers_by_narrative[narrative_id].narrative.tam_structure
                ),
            )
            for narrative_id in narrative_ids
        ),
        rows=tuple(
            NarrativeComparisonRow(
                metric_id=metric_id,
                values=tuple(
                    _finite_metric_value(
                        narrative_id=narrative_id,
                        metric_id=metric_id,
                        value=metrics_by_narrative[narrative_id][metric_id],
                    )
                    for narrative_id in narrative_ids
                ),
            )
            for metric_id in metric_ids
        ),
    )


def _containers_by_narrative_id(
    containers: Iterable[NarrativeContainer],
) -> dict[str, NarrativeContainer]:
    containers_by_narrative: dict[str, NarrativeContainer] = {}
    for container in containers:
        narrative_id = container.narrative.narrative_id
        if narrative_id in containers_by_narrative:
            raise ValueError("containers must have unique narrative ids")
        containers_by_narrative[narrative_id] = container
    if not containers_by_narrative:
        raise ValueError("containers must be non-empty")
    return containers_by_narrative


def _validate_narrative_ids(
    narrative_ids: tuple[str, ...],
    metrics_by_narrative: MetricMapByNarrative,
) -> None:
    if set(metrics_by_narrative) != set(narrative_ids):
        raise ValueError("metrics_by_narrative must match compared narrative ids")


def _metric_ids(
    metrics_by_narrative: MetricMapByNarrative,
    narrative_ids: tuple[str, ...],
) -> tuple[str, ...]:
    first_metric_ids = set(metrics_by_narrative[narrative_ids[0]])
    if not first_metric_ids:
        raise ValueError("metric maps must be non-empty")
    for narrative_id in narrative_ids:
        if set(metrics_by_narrative[narrative_id]) != first_metric_ids:
            raise ValueError("metric ids must match for every narrative")
    return tuple(sorted(first_metric_ids))


def _finite_metric_value(
    *,
    narrative_id: str,
    metric_id: str,
    value: float,
) -> float:
    if not isfinite(value):
        raise ValueError(f"metric value for {metric_id} in {narrative_id} must be finite")
    return float(value)
