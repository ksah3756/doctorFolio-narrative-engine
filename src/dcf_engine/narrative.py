"""Phase 1 Narrative container for deterministic valuation inputs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from math import isclose, isfinite
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from dcf_engine.assumption import (
    REINVESTMENT_TOOL_BY_STAGE,
    AssumptionState,
    ReinvestmentTool,
)
from dcf_engine.lifecycle import LifecycleStage

DEFAULT_NARRATIVE_ID: Final = "base"
PROBABILITY_SUM: Final = 1.0
PROBABILITY_SUM_TOLERANCE: Final = 1e-9

type ClaimModality = Literal["FACT", "INTERPRETATION", "PROJECTION"]
type ClaimActivationMask = Mapping[str, bool]
type TamStructure = Mapping[str, object]
type ScenarioValue = float | NDArray[np.float64]


@dataclass(frozen=True)
class Narrative:
    narrative_id: str = DEFAULT_NARRATIVE_ID
    lifecycle_stage: LifecycleStage = "growth"
    tam_structure: TamStructure = field(default_factory=dict)
    claim_activation_mask: ClaimActivationMask = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.narrative_id:
            raise ValueError("narrative_id must be non-empty")

    @classmethod
    def default(
        cls,
        *,
        narrative_id: str = DEFAULT_NARRATIVE_ID,
        lifecycle_stage: LifecycleStage = "growth",
        tam_structure: TamStructure | None = None,
        claim_activation_mask: ClaimActivationMask | None = None,
    ) -> Narrative:
        return cls(
            narrative_id=narrative_id,
            lifecycle_stage=lifecycle_stage,
            tam_structure={} if tam_structure is None else dict(tam_structure),
            claim_activation_mask=(
                {} if claim_activation_mask is None else dict(claim_activation_mask)
            ),
        )

    @property
    def reinvestment_tool(self) -> ReinvestmentTool:
        return REINVESTMENT_TOOL_BY_STAGE[self.lifecycle_stage]


def create_narrative(
    *,
    narrative_id: str = DEFAULT_NARRATIVE_ID,
    lifecycle_stage: LifecycleStage = "growth",
    tam_structure: TamStructure | None = None,
    claim_activation_mask: ClaimActivationMask | None = None,
    reinvestment_model: object | None = None,
) -> Narrative:
    if reinvestment_model is not None:
        raise ValueError("reinvestment_model is derived from lifecycle_stage")
    return Narrative.default(
        narrative_id=narrative_id,
        lifecycle_stage=lifecycle_stage,
        tam_structure=tam_structure,
        claim_activation_mask=claim_activation_mask,
    )


@dataclass(frozen=True)
class NarrativeContainer:
    narrative: Narrative
    assumptions_by_narrative: Mapping[str, tuple[AssumptionState, ...]]

    def __post_init__(self) -> None:
        if self.narrative.narrative_id not in self.assumptions_by_narrative:
            raise ValueError("assumptions_by_narrative must include narrative_id")

    @classmethod
    def single(
        cls,
        *,
        assumptions: Iterable[AssumptionState],
        narrative: Narrative | None = None,
    ) -> NarrativeContainer:
        active_narrative = Narrative.default() if narrative is None else narrative
        return cls(
            narrative=active_narrative,
            assumptions_by_narrative={
                active_narrative.narrative_id: tuple(assumptions),
            },
        )

    def assumptions_for(self, narrative_id: str) -> list[AssumptionState]:
        return list(self.assumptions_by_narrative[narrative_id])

    @property
    def active_assumptions(self) -> list[AssumptionState]:
        return self.assumptions_for(self.narrative.narrative_id)


@dataclass(frozen=True)
class NarrativeScenarioSet:
    containers_by_narrative: Mapping[str, NarrativeContainer]
    probabilities_by_narrative: Mapping[str, float]

    def __post_init__(self) -> None:
        scenario_ids = set(self.containers_by_narrative)
        if not scenario_ids:
            raise ValueError("containers_by_narrative must be non-empty")
        if scenario_ids != set(self.probabilities_by_narrative):
            raise ValueError("probabilities_by_narrative must match scenario narrative ids")
        for narrative_id, container in self.containers_by_narrative.items():
            if container.narrative.narrative_id != narrative_id:
                raise ValueError("containers_by_narrative keys must match container narrative ids")
        self._validate_measurement_axis()
        self._validate_probabilities()

    @classmethod
    def from_containers(
        cls,
        *,
        containers: Iterable[NarrativeContainer],
        probabilities_by_narrative: Mapping[str, float],
    ) -> NarrativeScenarioSet:
        containers_by_narrative: dict[str, NarrativeContainer] = {}
        for container in containers:
            narrative_id = container.narrative.narrative_id
            if narrative_id in containers_by_narrative:
                raise ValueError("containers must have unique narrative ids")
            containers_by_narrative[narrative_id] = container
        return cls(
            containers_by_narrative=containers_by_narrative,
            probabilities_by_narrative=dict(probabilities_by_narrative),
        )

    @classmethod
    def single(cls, container: NarrativeContainer) -> NarrativeScenarioSet:
        narrative_id = container.narrative.narrative_id
        return cls.from_containers(
            containers=(container,),
            probabilities_by_narrative={narrative_id: PROBABILITY_SUM},
        )

    @property
    def narrative_ids(self) -> tuple[str, ...]:
        return tuple(self.containers_by_narrative)

    def container_for(self, narrative_id: str) -> NarrativeContainer:
        return self.containers_by_narrative[narrative_id]

    def probability_weighted_value(
        self,
        values_by_narrative: Mapping[str, ScenarioValue],
    ) -> ScenarioValue:
        self._validate_value_ids(values_by_narrative)
        self._validate_value_shapes(values_by_narrative)
        weighted_value: NDArray[np.float64] | None = None
        scalar_only = True
        # valuation 출력의 shape를 보존하려고 첫 값 기준 누적 배열을 만든다.
        for narrative_id, probability in self.probabilities_by_narrative.items():
            value = values_by_narrative[narrative_id]
            value_array = np.asarray(value, dtype=np.float64)
            scalar_only = scalar_only and value_array.ndim == 0
            contribution = probability * value_array
            if weighted_value is None:
                weighted_value = np.asarray(contribution, dtype=np.float64)
            else:
                weighted_value = np.asarray(weighted_value + contribution, dtype=np.float64)
        assert weighted_value is not None
        if not np.all(np.isfinite(weighted_value)):
            raise ValueError("probability-weighted value must be finite")
        if scalar_only:
            return float(weighted_value)
        return weighted_value

    def _validate_probabilities(self) -> None:
        probabilities = tuple(self.probabilities_by_narrative.values())
        if not all(isfinite(probability) and probability >= 0.0 for probability in probabilities):
            raise ValueError("probabilities must be finite and non-negative")
        if not isclose(
            sum(probabilities),
            PROBABILITY_SUM,
            rel_tol=0.0,
            abs_tol=PROBABILITY_SUM_TOLERANCE,
        ):
            raise ValueError("probabilities must sum to 1.0")

    def _validate_measurement_axis(self) -> None:
        first_container = next(iter(self.containers_by_narrative.values()))
        lifecycle_stage = first_container.narrative.lifecycle_stage
        tam_structure = first_container.narrative.tam_structure
        # Type-1만 확률가중할 수 있으므로 구조 축이 달라지는 Type-2 혼합은 여기서 차단한다.
        for container in self.containers_by_narrative.values():
            narrative = container.narrative
            if (
                narrative.lifecycle_stage != lifecycle_stage
                or narrative.tam_structure != tam_structure
            ):
                raise ValueError(
                    "scenarios must share one measurement axis "
                    "(same lifecycle_stage and tam_structure); "
                    "probability-weighted mixing across axes is a category error"
                )

    def _validate_value_ids(self, values_by_narrative: Mapping[str, ScenarioValue]) -> None:
        if set(values_by_narrative) != set(self.containers_by_narrative):
            raise ValueError("values_by_narrative must match scenario narrative ids")

    def _validate_value_shapes(self, values_by_narrative: Mapping[str, ScenarioValue]) -> None:
        non_scalar_shape: tuple[int, ...] | None = None
        for value in values_by_narrative.values():
            value_array = np.asarray(value, dtype=np.float64)
            if value_array.ndim == 0:
                continue
            if non_scalar_shape is None:
                non_scalar_shape = value_array.shape
                continue
            if value_array.shape != non_scalar_shape:
                raise ValueError("non-scalar scenario values must share the same shape")


def build_claim_activation_mask(
    *,
    claim_modalities: Mapping[str, ClaimModality],
    selected_claim_ids: Iterable[str],
) -> dict[str, bool]:
    selected = set(selected_claim_ids)
    return {
        claim_id: modality == "FACT" or claim_id in selected
        for claim_id, modality in claim_modalities.items()
    }
