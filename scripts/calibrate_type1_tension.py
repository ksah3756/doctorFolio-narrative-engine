"""Emit deterministic Type-1 tension calibration JSON."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dcf_engine.narrative_calibration import (  # noqa: E402
    Type1CalibrationScenario,
    Type1CalibrationThresholdGrid,
    build_type1_tension_calibration_report,
    default_type1_tension_calibration_scenarios,
    report_to_json,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    scenarios = _selected_scenarios(args.scenario)
    threshold_grid = _threshold_grid(args)
    report = build_type1_tension_calibration_report(
        scenarios=scenarios,
        threshold_grid=threshold_grid,
        max_axes=args.max_axes,
    )
    sys.stdout.write(report_to_json(report))
    return 0


def _threshold_grid(args: argparse.Namespace) -> Type1CalibrationThresholdGrid:
    default_grid = Type1CalibrationThresholdGrid()
    return Type1CalibrationThresholdGrid(
        contested_mass_thresholds=tuple(args.contested_mass_threshold)
        or default_grid.contested_mass_thresholds,
        stability_thresholds=tuple(args.stability_threshold)
        or default_grid.stability_thresholds,
        explained_variance_thresholds=tuple(args.explained_variance_threshold)
        or default_grid.explained_variance_thresholds,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic Type-1 tension threshold calibration report",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Fixture scenario id to include; repeat to include multiple scenarios",
    )
    parser.add_argument(
        "--contested-mass-threshold",
        action="append",
        type=float,
        default=[],
        help="Stage A/C contested mass threshold; repeat to sweep",
    )
    parser.add_argument(
        "--stability-threshold",
        action="append",
        type=float,
        default=[],
        help="Leave-one-out stability threshold; repeat to sweep",
    )
    parser.add_argument(
        "--explained-variance-threshold",
        action="append",
        type=float,
        default=[],
        help="Cumulative PCA explained-variance threshold; repeat to sweep",
    )
    parser.add_argument(
        "--max-axes",
        type=int,
        default=3,
        help="Maximum axes to promote per threshold combination",
    )
    return parser


def _selected_scenarios(
    requested_scenario_ids: Sequence[str],
) -> tuple[Type1CalibrationScenario, ...]:
    scenarios = default_type1_tension_calibration_scenarios()
    if not requested_scenario_ids:
        return scenarios

    by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    unknown_ids = sorted(set(requested_scenario_ids) - set(by_id))
    if unknown_ids:
        raise ValueError(f"unknown scenario ids: {', '.join(unknown_ids)}")
    return tuple(by_id[scenario_id] for scenario_id in sorted(set(requested_scenario_ids)))


if __name__ == "__main__":
    raise SystemExit(main())
