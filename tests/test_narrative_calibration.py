from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dcf_engine.narrative_axes import ClaimAssumptionPull
from dcf_engine.narrative_calibration import (
    Type1CalibrationScenario,
    Type1CalibrationThresholdGrid,
    build_type1_tension_calibration_report,
    default_type1_tension_calibration_scenarios,
    report_to_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_stable_bipolar_fixture_records_promoted_axis() -> None:
    report = build_type1_tension_calibration_report(
        scenarios=[_stable_bipolar_scenario()],
        threshold_grid=Type1CalibrationThresholdGrid(
            contested_mass_thresholds=(1.0,),
            stability_thresholds=(0.70,),
            explained_variance_thresholds=(0.80,),
        ),
    )

    assert report.summary == {
        "scenario_count": 1,
        "row_count": 1,
        "promoted_count": 1,
        "rejected_count": 0,
    }
    row = report.rows[0]
    assert row.scenario_id == "stable-bipolar"
    assert row.axis_count == 1
    assert row.rejection_reason is None
    assert row.promoted_loadings == ({"margin": 1.0},)


def test_reject_fixtures_record_clear_no_axis_reasons() -> None:
    report = build_type1_tension_calibration_report(
        scenarios=[
            _unanimous_one_sided_scenario(),
            _unstable_two_claim_scenario(),
            _thin_outlier_scenario(),
        ],
        threshold_grid=Type1CalibrationThresholdGrid(
            contested_mass_thresholds=(0.50,),
            stability_thresholds=(0.70,),
            explained_variance_thresholds=(0.80,),
        ),
    )

    reasons = {row.scenario_id: row.rejection_reason for row in report.rows}

    assert reasons == {
        "unanimous-one-sided": "stage_a_bipolar_mass_below_threshold",
        "unstable-two-claim": "stability_below_threshold",
        "thin-outlier-driven": "stage_c_bipolar_mass_below_threshold",
    }
    assert all(row.axis_count == 0 for row in report.rows)
    assert report.summary["promoted_count"] == 0
    assert report.summary["rejected_count"] == 3


def test_reversing_pull_order_preserves_row_ordering_and_json() -> None:
    scenario = _stable_bipolar_scenario()
    reversed_scenario = Type1CalibrationScenario(
        scenario_id=scenario.scenario_id,
        pulls=tuple(reversed(scenario.pulls)),
    )
    grid = Type1CalibrationThresholdGrid(
        contested_mass_thresholds=(1.0, 0.50),
        stability_thresholds=(0.90, 0.70),
        explained_variance_thresholds=(1.0, 0.80),
    )

    forward_report = build_type1_tension_calibration_report(
        scenarios=[scenario],
        threshold_grid=grid,
    )
    reversed_report = build_type1_tension_calibration_report(
        scenarios=[reversed_scenario],
        threshold_grid=grid,
    )

    assert [row.row_key for row in forward_report.rows] == sorted(
        row.row_key for row in forward_report.rows
    )
    assert report_to_json(forward_report) == report_to_json(reversed_report)


def test_invalid_threshold_grids_fail_with_clear_validation_errors() -> None:
    with pytest.raises(ValueError, match="contested_mass_thresholds must be non-empty"):
        Type1CalibrationThresholdGrid(
            contested_mass_thresholds=(),
            stability_thresholds=(0.70,),
            explained_variance_thresholds=(0.80,),
        )

    with pytest.raises(ValueError, match="stability_thresholds must be finite and in \\[0, 1\\]"):
        Type1CalibrationThresholdGrid(
            contested_mass_thresholds=(1.0,),
            stability_thresholds=(1.20,),
            explained_variance_thresholds=(0.80,),
        )

    with pytest.raises(
        ValueError,
        match="explained_variance_thresholds must be finite and in \\(0, 1\\]",
    ):
        Type1CalibrationThresholdGrid(
            contested_mass_thresholds=(1.0,),
            stability_thresholds=(0.70,),
            explained_variance_thresholds=(0.0,),
        )


def test_cli_outputs_json_with_rows_and_summary_counts() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/calibrate_type1_tension.py"),
            "--scenario",
            "stable-bipolar",
            "--contested-mass-threshold",
            "1.0",
            "--stability-threshold",
            "0.70",
            "--explained-variance-threshold",
            "0.80",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["summary"] == {
        "scenario_count": 1,
        "row_count": 1,
        "promoted_count": 1,
        "rejected_count": 0,
    }
    row = payload["rows"][0]
    assert row["scenario_id"] == "stable-bipolar"
    assert row["thresholds"] == {
        "contested_mass": 1.0,
        "stability": 0.70,
        "explained_variance": 0.80,
    }
    assert row["axis_count"] == 1
    assert row["promoted_loadings"] == [{"margin": 1.0}]
    assert row["rejection_reason"] is None


def test_default_fixture_set_covers_stable_and_reject_cases() -> None:
    report = build_type1_tension_calibration_report(
        scenarios=default_type1_tension_calibration_scenarios(),
        threshold_grid=Type1CalibrationThresholdGrid(
            contested_mass_thresholds=(1.0,),
            stability_thresholds=(0.70,),
            explained_variance_thresholds=(0.80,),
        ),
    )

    scenario_ids = {row.scenario_id for row in report.rows}

    assert {
        "stable-bipolar",
        "unanimous-one-sided",
        "unstable-two-claim",
        "thin-outlier-driven",
    }.issubset(scenario_ids)


def _stable_bipolar_scenario() -> Type1CalibrationScenario:
    return Type1CalibrationScenario(
        scenario_id="stable-bipolar",
        pulls=(
            ClaimAssumptionPull(claim_id="positive-1", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="positive-2", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="positive-3", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="negative-1", assumption_id="margin", pull=-1.0),
            ClaimAssumptionPull(claim_id="negative-2", assumption_id="margin", pull=-1.0),
        ),
    )


def _unanimous_one_sided_scenario() -> Type1CalibrationScenario:
    return Type1CalibrationScenario(
        scenario_id="unanimous-one-sided",
        pulls=(
            ClaimAssumptionPull(claim_id="positive-1", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="positive-2", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="positive-3", assumption_id="margin", pull=1.0),
        ),
    )


def _unstable_two_claim_scenario() -> Type1CalibrationScenario:
    return Type1CalibrationScenario(
        scenario_id="unstable-two-claim",
        pulls=(
            ClaimAssumptionPull(claim_id="positive", assumption_id="margin", pull=1.0),
            ClaimAssumptionPull(claim_id="negative", assumption_id="margin", pull=-1.0),
        ),
    )


def _thin_outlier_scenario() -> Type1CalibrationScenario:
    return Type1CalibrationScenario(
        scenario_id="thin-outlier-driven",
        pulls=(
            ClaimAssumptionPull(claim_id="majority-1", assumption_id="revenue_cagr", pull=1.0),
            ClaimAssumptionPull(claim_id="majority-2", assumption_id="revenue_cagr", pull=1.1),
            ClaimAssumptionPull(claim_id="majority-3", assumption_id="revenue_cagr", pull=0.9),
            ClaimAssumptionPull(
                claim_id="thin-outlier",
                assumption_id="revenue_cagr",
                pull=-20.0,
                weight=0.10,
            ),
        ),
    )
