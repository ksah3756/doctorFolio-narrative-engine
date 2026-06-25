"""Phase 1 Narrative container for deterministic valuation inputs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from dcf_engine.assumption import (
    REINVESTMENT_TOOL_BY_STAGE,
    AssumptionState,
    ReinvestmentTool,
)
from dcf_engine.lifecycle import LifecycleStage

DEFAULT_NARRATIVE_ID: Final = "base"

type ClaimModality = Literal["FACT", "INTERPRETATION", "PROJECTION"]
type ClaimActivationMask = Mapping[str, bool]
type TamStructure = Mapping[str, object]


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
