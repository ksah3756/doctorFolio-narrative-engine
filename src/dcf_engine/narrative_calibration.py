"""Deterministic Type-1 tension threshold calibration reports."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from dcf_engine._narrative_calibration_fixtures import DEFAULT_TYPE1_SCENARIO_SPECS
from dcf_engine.narrative_axes import (
    DEFAULT_AXIS_STABILITY_THRESHOLD,
    DEFAULT_CONTESTED_MASS_THRESHOLD,
    DEFAULT_EXPLAINED_VARIANCE_THRESHOLD,
    DEFAULT_MAX_AXES,
    ClaimAssumptionPull,
    Type1AxisRejectionReason,
    Type1CandidateComponentDiagnostic,
    generate_type1_tension_axis_diagnostics,
)

type Type1CalibrationRejectionReason = Type1AxisRejectionReason | Literal[
    "stage_a_bipolar_mass_below_threshold",
    "zero_variance_after_centering",
]


@dataclass(frozen=True)
class Type1CalibrationScenario:
    scenario_id: str
    pulls: tuple[ClaimAssumptionPull, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-empty")
        if not self.pulls:
            raise ValueError("scenario pulls must be non-empty")
        object.__setattr__(self, "pulls", tuple(self.pulls))


@dataclass(frozen=True)
class Type1CalibrationThresholdGrid:
    contested_mass_thresholds: tuple[float, ...] = (DEFAULT_CONTESTED_MASS_THRESHOLD,)
    stability_thresholds: tuple[float, ...] = (DEFAULT_AXIS_STABILITY_THRESHOLD,)
    explained_variance_thresholds: tuple[float, ...] = (
        DEFAULT_EXPLAINED_VARIANCE_THRESHOLD,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contested_mass_thresholds",
            _sorted_unique_thresholds(
                name="contested_mass_thresholds",
                values=self.contested_mass_thresholds,
                bounds_message="must be finite and positive",
                is_valid=lambda value: isfinite(value) and value > 0.0,
            ),
        )
        object.__setattr__(
            self,
            "stability_thresholds",
            _sorted_unique_thresholds(
                name="stability_thresholds",
                values=self.stability_thresholds,
                bounds_message="must be finite and in [0, 1]",
                is_valid=lambda value: isfinite(value) and 0.0 <= value <= 1.0,
            ),
        )
        object.__setattr__(
            self,
            "explained_variance_thresholds",
            _sorted_unique_thresholds(
                name="explained_variance_thresholds",
                values=self.explained_variance_thresholds,
                bounds_message="must be finite and in (0, 1]",
                is_valid=lambda value: isfinite(value) and 0.0 < value <= 1.0,
            ),
        )


@dataclass(frozen=True)
class Type1CalibrationRow:
    scenario_id: str
    contested_mass_threshold: float
    stability_threshold: float
    explained_variance_threshold: float
    axis_count: int
    promoted_loadings: tuple[dict[str, float], ...]
    rejection_reason: Type1CalibrationRejectionReason | None
    pca_explained_variance_ratios: tuple[float, ...]

    @property
    def row_key(self) -> tuple[str, float, float, float]:
        return (
            self.scenario_id,
            self.contested_mass_threshold,
            self.stability_threshold,
            self.explained_variance_threshold,
        )


@dataclass(frozen=True)
class Type1CalibrationReport:
    rows: tuple[Type1CalibrationRow, ...]
    summary: dict[str, int]


def build_type1_tension_calibration_report(
    *,
    scenarios: Sequence[Type1CalibrationScenario],
    threshold_grid: Type1CalibrationThresholdGrid,
    max_axes: int = DEFAULT_MAX_AXES,
) -> Type1CalibrationReport:
    """Sweep deterministic Type-1 gate thresholds over fixture scenarios."""

    if not scenarios:
        raise ValueError("calibration scenarios must be non-empty")
    if max_axes < 1:
        raise ValueError("max_axes must be at least 1")

    rows = tuple(
        sorted(
            (
                _calibration_row(
                    scenario=scenario,
                    contested_mass_threshold=contested_mass_threshold,
                    stability_threshold=stability_threshold,
                    explained_variance_threshold=explained_variance_threshold,
                    max_axes=max_axes,
                )
                for scenario in sorted(scenarios, key=lambda item: item.scenario_id)
                for contested_mass_threshold in threshold_grid.contested_mass_thresholds
                for stability_threshold in threshold_grid.stability_thresholds
                for explained_variance_threshold in threshold_grid.explained_variance_thresholds
            ),
            key=lambda row: row.row_key,
        )
    )
    return Type1CalibrationReport(rows=rows, summary=_summary(rows, scenarios))


def default_type1_tension_calibration_scenarios() -> tuple[Type1CalibrationScenario, ...]:
    """Return replay-only fixtures for v6.1 Type-1 gate calibration."""

    return tuple(
        Type1CalibrationScenario(
            scenario_id=scenario_id,
            pulls=tuple(
                ClaimAssumptionPull(
                    claim_id=claim_id,
                    assumption_id=assumption_id,
                    pull=pull,
                    weight=weight,
                )
                for claim_id, assumption_id, pull, weight in pull_specs
            ),
        )
        for scenario_id, pull_specs in DEFAULT_TYPE1_SCENARIO_SPECS
    )


def report_to_json(report: Type1CalibrationReport) -> str:
    from dcf_engine._narrative_calibration_json import report_to_json as serialize_report

    return serialize_report(report)


def _calibration_row(
    *,
    scenario: Type1CalibrationScenario,
    contested_mass_threshold: float,
    stability_threshold: float,
    explained_variance_threshold: float,
    max_axes: int,
) -> Type1CalibrationRow:
    diagnostics = generate_type1_tension_axis_diagnostics(
        scenario.pulls,
        contested_mass_threshold=contested_mass_threshold,
        stability_threshold=stability_threshold,
        explained_variance_threshold=explained_variance_threshold,
        max_axes=max_axes,
    )
    promoted_loadings = tuple(
        dict(sorted(axis.loadings.items())) for axis in diagnostics.promoted_axes
    )

    return Type1CalibrationRow(
        scenario_id=scenario.scenario_id,
        contested_mass_threshold=contested_mass_threshold,
        stability_threshold=stability_threshold,
        explained_variance_threshold=explained_variance_threshold,
        axis_count=len(diagnostics.promoted_axes),
        promoted_loadings=promoted_loadings,
        rejection_reason=_row_rejection_reason(
            axis_count=len(diagnostics.promoted_axes),
            has_stage_a_pass=any(gate.passes for gate in diagnostics.assumption_mass_gates),
            candidate_components=diagnostics.candidate_components,
        ),
        pca_explained_variance_ratios=diagnostics.pca_explained_variance_ratios,
    )


def _row_rejection_reason(
    *,
    axis_count: int,
    has_stage_a_pass: bool,
    candidate_components: Sequence[Type1CandidateComponentDiagnostic],
) -> Type1CalibrationRejectionReason | None:
    if axis_count > 0:
        return None
    if not has_stage_a_pass:
        return "stage_a_bipolar_mass_below_threshold"
    if not candidate_components:
        return "zero_variance_after_centering"
    first_reason = candidate_components[0].rejection_reason
    if first_reason is None:
        return "zero_variance_after_centering"
    return first_reason


def _sorted_unique_thresholds(
    *,
    name: str,
    values: Sequence[float],
    bounds_message: str,
    is_valid: Callable[[float], bool],
) -> tuple[float, ...]:
    if not values:
        raise ValueError(f"{name} must be non-empty")
    validated: list[float] = []
    for value in values:
        if not is_valid(value):
            raise ValueError(f"{name} {bounds_message}")
        validated.append(float(value))
    return tuple(sorted(set(validated)))


def _summary(
    rows: Sequence[Type1CalibrationRow],
    scenarios: Sequence[Type1CalibrationScenario],
) -> dict[str, int]:
    promoted_count = sum(1 for row in rows if row.axis_count > 0)
    return {
        "scenario_count": len({scenario.scenario_id for scenario in scenarios}),
        "row_count": len(rows),
        "promoted_count": promoted_count,
        "rejected_count": len(rows) - promoted_count,
    }

