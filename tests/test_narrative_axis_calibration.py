from __future__ import annotations

import math

import pytest

from dcf_engine.narrative_axes import ClaimAssumptionPull, generate_type1_tension_axes
from dcf_engine.narrative_axis_calibration import (
    Type1AxisCalibrationCase,
    Type1AxisControlSet,
    calibrate_type1_axis_controls,
)


def test_candidate_control_sets_evaluate_in_stable_order() -> None:
    conservative = Type1AxisControlSet(
        control_id="conservative",
        contested_mass_threshold=1.0,
        explained_variance_threshold=0.80,
        stability_threshold=0.70,
        max_axes=1,
    )
    permissive = Type1AxisControlSet(
        control_id="permissive",
        contested_mass_threshold=0.50,
        explained_variance_threshold=1.0,
        stability_threshold=0.0,
        max_axes=2,
    )

    result = calibrate_type1_axis_controls(
        cases=tuple(reversed((_stable_case(), _reject_case()))),
        control_sets=(conservative, permissive),
    )

    assert [evaluation.control_id for evaluation in result.evaluations] == [
        "conservative",
        "permissive",
    ]
    assert [
        (row.control_id, row.case_id)
        for evaluation in result.evaluations
        for row in evaluation.rows
    ] == [
        ("conservative", "reject-one-sided"),
        ("conservative", "stable-bipolar"),
        ("permissive", "reject-one-sided"),
        ("permissive", "stable-bipolar"),
    ]


def test_fixture_rows_record_axis_counts_and_rejection_reasons() -> None:
    result = calibrate_type1_axis_controls(
        cases=(_stable_case(), _reject_case()),
        control_sets=(
            Type1AxisControlSet(
                control_id="defaults",
                contested_mass_threshold=1.0,
                explained_variance_threshold=0.80,
                stability_threshold=0.70,
                max_axes=3,
            ),
        ),
    )

    rows = {row.case_id: row for row in result.evaluations[0].rows}

    assert rows["stable-bipolar"].axis_count == 1
    assert rows["stable-bipolar"].passed is True
    assert rows["stable-bipolar"].rejection_reason is None
    assert rows["reject-one-sided"].axis_count == 0
    assert rows["reject-one-sided"].passed is True
    assert rows["reject-one-sided"].rejection_reason == "stage_a_bipolar_mass_below_threshold"


def test_equal_scoring_candidates_tie_break_by_explicit_control_order() -> None:
    first = Type1AxisControlSet(
        control_id="first",
        contested_mass_threshold=1.0,
        explained_variance_threshold=0.80,
        stability_threshold=0.70,
        max_axes=3,
    )
    second = Type1AxisControlSet(
        control_id="second",
        contested_mass_threshold=1.0,
        explained_variance_threshold=0.80,
        stability_threshold=0.70,
        max_axes=3,
    )

    result = calibrate_type1_axis_controls(
        cases=(_stable_case(), _reject_case()),
        control_sets=(second, first),
    )

    assert [evaluation.score for evaluation in result.evaluations] == [2, 2]
    assert result.selected_controls.control_id == "second"
    assert result.selected_evaluation.control_order == 0


def test_invalid_or_non_finite_control_values_fail_clearly() -> None:
    with pytest.raises(ValueError, match="contested_mass_threshold must be finite and positive"):
        Type1AxisControlSet(
            control_id="bad-contested-mass",
            contested_mass_threshold=math.nan,
            explained_variance_threshold=0.80,
            stability_threshold=0.70,
            max_axes=3,
        )

    with pytest.raises(ValueError, match="stability_threshold must be finite and in \\[0, 1\\]"):
        Type1AxisControlSet(
            control_id="bad-stability",
            contested_mass_threshold=1.0,
            explained_variance_threshold=0.80,
            stability_threshold=math.inf,
            max_axes=3,
        )


def test_existing_type1_axis_generator_default_behavior_stays_unchanged() -> None:
    axes = generate_type1_tension_axes(_stable_pulls())

    assert len(axes) == 1
    assert axes[0].axis_index == 0
    assert axes[0].explained_variance_ratio == 1.0
    assert axes[0].loadings == {"margin": 1.0}


def _stable_case() -> Type1AxisCalibrationCase:
    return Type1AxisCalibrationCase(
        case_id="stable-bipolar",
        pulls=_stable_pulls(),
        expected_axis_count=1,
    )


def _reject_case() -> Type1AxisCalibrationCase:
    return Type1AxisCalibrationCase(
        case_id="reject-one-sided",
        pulls=(
            ClaimAssumptionPull(claim_id="positive-1", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="positive-2", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="positive-3", assumption_id="margin", pull=1.0),
        ),
        expected_axis_count=0,
        expected_rejection_reason="stage_a_bipolar_mass_below_threshold",
    )


def _stable_pulls() -> tuple[ClaimAssumptionPull, ...]:
    return (
        ClaimAssumptionPull(claim_id="positive-1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="positive-2", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="positive-3", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="negative-1", assumption_id="margin", pull=-1.0),
        ClaimAssumptionPull(claim_id="negative-2", assumption_id="margin", pull=-1.0),
    )
