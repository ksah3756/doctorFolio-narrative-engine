"""JSON serialization for Type-1 calibration reports."""

from __future__ import annotations

import json

from dcf_engine.narrative_calibration import Type1CalibrationReport, Type1CalibrationRow


def report_to_json(report: Type1CalibrationReport) -> str:
    return json.dumps(_report_payload(report), indent=2, sort_keys=True) + "\n"


def _report_payload(report: Type1CalibrationReport) -> dict[str, object]:
    return {
        "summary": report.summary,
        "rows": [_row_payload(row) for row in report.rows],
    }


def _row_payload(row: Type1CalibrationRow) -> dict[str, object]:
    return {
        "scenario_id": row.scenario_id,
        "thresholds": {
            "contested_mass": row.contested_mass_threshold,
            "stability": row.stability_threshold,
            "explained_variance": row.explained_variance_threshold,
        },
        "axis_count": row.axis_count,
        "promoted_loadings": list(row.promoted_loadings),
        "rejection_reason": row.rejection_reason,
        "pca_explained_variance_ratios": list(row.pca_explained_variance_ratios),
    }
