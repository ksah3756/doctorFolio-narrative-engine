from __future__ import annotations

from typing import cast

import pytest

from dcf_engine.extraction.calibration import CalibrationGroup, CalibrationResult
from dcf_engine.narrative_axes import (
    ClaimAssumptionPull,
    generate_type1_tension_axes,
    generate_type1_tension_axes_with_calibration,
)


def test_failed_calibration_blocks_type1_axis_promotion() -> None:
    pulls = _axis_promoting_pulls()

    ungated_axes = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )
    gated_result = generate_type1_tension_axes_with_calibration(
        pulls,
        calibration_result=_failed_calibration(),
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )

    assert ungated_axes != ()
    assert gated_result.axes == ()
    assert gated_result.blocked_by_calibration is True


def test_passing_calibration_permits_same_type1_axes_as_existing_generator() -> None:
    pulls = _axis_promoting_pulls()

    expected_axes = generate_type1_tension_axes(
        pulls,
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )
    gated_result = generate_type1_tension_axes_with_calibration(
        pulls,
        calibration_result=_passing_calibration(),
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )

    assert gated_result.axes == expected_axes
    assert gated_result.blocked_by_calibration is False


def test_blocked_type1_calibration_result_exposes_deterministic_audit_data() -> None:
    gated_result = generate_type1_tension_axes_with_calibration(
        _axis_promoting_pulls(),
        calibration_result=_failed_calibration(),
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )

    audit = gated_result.calibration_audit
    assert audit.threshold == 0.9
    assert audit.overall_agreement_rate == 0.8
    assert audit.invalid_repeat_count == 1
    assert audit.unstable_group_identifiers == (
        ("chunk-a", "gross margin improved year over year"),
        ("chunk-b", "pricing weakened in gaming"),
    )


def test_existing_type1_axis_generator_remains_calibration_optional() -> None:
    axes = generate_type1_tension_axes(
        _axis_promoting_pulls(),
        contested_mass_threshold=1.0,
        stability_threshold=0.0,
    )

    assert len(axes) == 1


def test_missing_calibration_result_fails_before_type1_promotion() -> None:
    with pytest.raises(ValueError, match="calibration_result is required"):
        generate_type1_tension_axes_with_calibration(
            _axis_promoting_pulls(),
            calibration_result=cast(CalibrationResult | None, None),
            contested_mass_threshold=1.0,
            stability_threshold=0.0,
        )


def test_structurally_insufficient_calibration_result_fails_before_type1_promotion() -> None:
    insufficient = CalibrationResult(
        passed=True,
        valid_repeat_count=1,
        invalid_repeat_count=0,
        agreement_rate=1.0,
        threshold=0.9,
        unstable_groups=(),
    )

    with pytest.raises(ValueError, match="at least two schema-valid calibration repeats"):
        generate_type1_tension_axes_with_calibration(
            _axis_promoting_pulls(),
            calibration_result=insufficient,
            contested_mass_threshold=1.0,
            stability_threshold=0.0,
        )


def _axis_promoting_pulls() -> tuple[ClaimAssumptionPull, ...]:
    return (
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="revenue_cagr", pull=2.0),
        ClaimAssumptionPull(claim_id="optimistic-1", assumption_id="margin", pull=1.0),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="revenue_cagr", pull=1.0),
        ClaimAssumptionPull(claim_id="optimistic-2", assumption_id="margin", pull=0.5),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="revenue_cagr", pull=-1.0),
        ClaimAssumptionPull(claim_id="pessimistic-1", assumption_id="margin", pull=-0.5),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="revenue_cagr", pull=-2.0),
        ClaimAssumptionPull(claim_id="pessimistic-2", assumption_id="margin", pull=-1.0),
    )


def _passing_calibration() -> CalibrationResult:
    return CalibrationResult(
        passed=True,
        valid_repeat_count=10,
        invalid_repeat_count=0,
        agreement_rate=1.0,
        threshold=0.9,
        unstable_groups=(),
    )


def _failed_calibration() -> CalibrationResult:
    return CalibrationResult(
        passed=False,
        valid_repeat_count=10,
        invalid_repeat_count=1,
        agreement_rate=0.8,
        threshold=0.9,
        unstable_groups=(
            CalibrationGroup(
                chunk_id="chunk-a",
                claim_group="gross margin improved year over year",
                valid_repeat_count=10,
                agreement_rate=0.8,
                threshold=0.9,
                label_counts={
                    ("COST_SIGNAL", "INCREASE"): 8,
                    ("COST_SIGNAL", "DECREASE"): 2,
                },
            ),
            CalibrationGroup(
                chunk_id="chunk-b",
                claim_group="pricing weakened in gaming",
                valid_repeat_count=10,
                agreement_rate=0.8,
                threshold=0.9,
                label_counts={
                    ("PRICING_SIGNAL", "DECREASE"): 8,
                    ("PRICING_SIGNAL", "INCREASE"): 2,
                },
            ),
        ),
    )
