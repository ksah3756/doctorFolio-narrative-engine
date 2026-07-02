"""Offline Type-1 axis control-set calibration harness."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Literal

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

type Type1AxisCalibrationRejectionReason = Type1AxisRejectionReason | Literal[
    "stage_a_bipolar_mass_below_threshold",
    "zero_variance_after_centering",
]


@dataclass(frozen=True)
class Type1AxisControlSet:
    control_id: str
    contested_mass_threshold: float = DEFAULT_CONTESTED_MASS_THRESHOLD
    explained_variance_threshold: float = DEFAULT_EXPLAINED_VARIANCE_THRESHOLD
    stability_threshold: float = DEFAULT_AXIS_STABILITY_THRESHOLD
    max_axes: int = DEFAULT_MAX_AXES

    def __post_init__(self) -> None:
        if not self.control_id.strip():
            raise ValueError("control_id must be non-empty")
        if not isfinite(self.contested_mass_threshold) or (
            self.contested_mass_threshold <= 0.0
        ):
            raise ValueError("contested_mass_threshold must be finite and positive")
        if not isfinite(self.explained_variance_threshold) or not (
            0.0 < self.explained_variance_threshold <= 1.0
        ):
            raise ValueError("explained_variance_threshold must be finite and in (0, 1]")
        if not isfinite(self.stability_threshold) or not (
            0.0 <= self.stability_threshold <= 1.0
        ):
            raise ValueError("stability_threshold must be finite and in [0, 1]")
        if self.max_axes < 1:
            raise ValueError("max_axes must be at least 1")


@dataclass(frozen=True)
class Type1AxisCalibrationCase:
    case_id: str
    pulls: tuple[ClaimAssumptionPull, ...]
    expected_axis_count: int
    expected_rejection_reason: Type1AxisCalibrationRejectionReason | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must be non-empty")
        if not self.pulls:
            raise ValueError("case pulls must be non-empty")
        if self.expected_axis_count < 0:
            raise ValueError("expected_axis_count must be non-negative")
        if self.expected_axis_count > 0 and self.expected_rejection_reason is not None:
            raise ValueError("promoting cases cannot expect a rejection reason")
        object.__setattr__(self, "pulls", tuple(self.pulls))


@dataclass(frozen=True)
class Type1AxisCalibrationRow:
    control_id: str
    control_order: int
    case_id: str
    passed: bool
    axis_count: int
    expected_axis_count: int
    rejection_reason: Type1AxisCalibrationRejectionReason | None
    expected_rejection_reason: Type1AxisCalibrationRejectionReason | None
    promoted_loadings: tuple[dict[str, float], ...]
    pca_explained_variance_ratios: tuple[float, ...]

    @property
    def row_key(self) -> tuple[int, str]:
        return (self.control_order, self.case_id)


@dataclass(frozen=True)
class Type1AxisControlEvaluation:
    controls: Type1AxisControlSet
    control_order: int
    rows: tuple[Type1AxisCalibrationRow, ...]

    @property
    def control_id(self) -> str:
        return self.controls.control_id

    @property
    def score(self) -> int:
        return sum(1 for row in self.rows if row.passed)

    @property
    def rejected_count(self) -> int:
        return len(self.rows) - self.score


@dataclass(frozen=True)
class Type1AxisControlCalibrationResult:
    selected_controls: Type1AxisControlSet
    selected_evaluation: Type1AxisControlEvaluation
    evaluations: tuple[Type1AxisControlEvaluation, ...]


def calibrate_type1_axis_controls(
    *,
    cases: Sequence[Type1AxisCalibrationCase],
    control_sets: Sequence[Type1AxisControlSet],
) -> Type1AxisControlCalibrationResult:
    """Score candidate Type-1 axis controls against deterministic fixture cases."""

    ordered_cases = _ordered_cases(cases)
    ordered_control_sets = _ordered_control_sets(control_sets)
    evaluations = tuple(
        _evaluate_control_set(
            controls=controls,
            control_order=control_order,
            cases=ordered_cases,
        )
        for control_order, controls in enumerate(ordered_control_sets)
    )
    selected = max(
        evaluations,
        key=lambda evaluation: (evaluation.score, -evaluation.control_order),
    )
    return Type1AxisControlCalibrationResult(
        selected_controls=selected.controls,
        selected_evaluation=selected,
        evaluations=evaluations,
    )


def _evaluate_control_set(
    *,
    controls: Type1AxisControlSet,
    control_order: int,
    cases: Sequence[Type1AxisCalibrationCase],
) -> Type1AxisControlEvaluation:
    rows = tuple(
        _evaluate_case(
            controls=controls,
            control_order=control_order,
            case=case,
        )
        for case in cases
    )
    return Type1AxisControlEvaluation(
        controls=controls,
        control_order=control_order,
        rows=rows,
    )


def _evaluate_case(
    *,
    controls: Type1AxisControlSet,
    control_order: int,
    case: Type1AxisCalibrationCase,
) -> Type1AxisCalibrationRow:
    diagnostics = generate_type1_tension_axis_diagnostics(
        case.pulls,
        contested_mass_threshold=controls.contested_mass_threshold,
        explained_variance_threshold=controls.explained_variance_threshold,
        stability_threshold=controls.stability_threshold,
        max_axes=controls.max_axes,
    )
    axis_count = len(diagnostics.promoted_axes)
    rejection_reason = _row_rejection_reason(
        axis_count=axis_count,
        has_stage_a_pass=any(gate.passes for gate in diagnostics.assumption_mass_gates),
        candidate_components=diagnostics.candidate_components,
    )
    return Type1AxisCalibrationRow(
        control_id=controls.control_id,
        control_order=control_order,
        case_id=case.case_id,
        passed=_case_passed(
            axis_count=axis_count,
            expected_axis_count=case.expected_axis_count,
            rejection_reason=rejection_reason,
            expected_rejection_reason=case.expected_rejection_reason,
        ),
        axis_count=axis_count,
        expected_axis_count=case.expected_axis_count,
        rejection_reason=rejection_reason,
        expected_rejection_reason=case.expected_rejection_reason,
        promoted_loadings=tuple(
            dict(sorted(axis.loadings.items())) for axis in diagnostics.promoted_axes
        ),
        pca_explained_variance_ratios=diagnostics.pca_explained_variance_ratios,
    )


def _case_passed(
    *,
    axis_count: int,
    expected_axis_count: int,
    rejection_reason: Type1AxisCalibrationRejectionReason | None,
    expected_rejection_reason: Type1AxisCalibrationRejectionReason | None,
) -> bool:
    if axis_count != expected_axis_count:
        return False
    if expected_rejection_reason is None:
        return True
    return rejection_reason == expected_rejection_reason


def _row_rejection_reason(
    *,
    axis_count: int,
    has_stage_a_pass: bool,
    candidate_components: Sequence[Type1CandidateComponentDiagnostic],
) -> Type1AxisCalibrationRejectionReason | None:
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


def _ordered_cases(
    cases: Sequence[Type1AxisCalibrationCase],
) -> tuple[Type1AxisCalibrationCase, ...]:
    if not cases:
        raise ValueError("calibration cases must be non-empty")
    case_ids = [case.case_id for case in cases]
    duplicate_case_ids = {case_id for case_id in case_ids if case_ids.count(case_id) > 1}
    if duplicate_case_ids:
        ordered_duplicates = ", ".join(sorted(duplicate_case_ids))
        raise ValueError(f"duplicate calibration case ids: {ordered_duplicates}")
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _ordered_control_sets(
    control_sets: Sequence[Type1AxisControlSet],
) -> tuple[Type1AxisControlSet, ...]:
    if not control_sets:
        raise ValueError("control_sets must be non-empty")
    control_ids = [controls.control_id for controls in control_sets]
    duplicate_control_ids = {
        control_id for control_id in control_ids if control_ids.count(control_id) > 1
    }
    if duplicate_control_ids:
        ordered_duplicates = ", ".join(sorted(duplicate_control_ids))
        raise ValueError(f"duplicate control ids: {ordered_duplicates}")
    return tuple(control_sets)
