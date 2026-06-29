#!/usr/bin/env python3
"""Run the loaded-claim NVDA validation cycle."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Final

from dcf_engine.ingestion import JsonClaimStore
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.validate_cycle import (
    DEFAULT_VALIDATION_ITERATIONS,
    DEFAULT_VALIDATION_SEED,
    run_validation_cycle,
)

DEFAULT_DATA_DIR: Final[Path] = Path("data")
DEFAULT_STAGE: Final[LifecycleStage] = "growth"
LIFECYCLE_STAGE_CHOICES: Final[tuple[LifecycleStage, ...]] = (
    "young",
    "growth",
    "mature",
    "decline",
)
USD_TO_TRILLIONS: Final[float] = 1_000_000_000_000.0


def main() -> None:
    args = _parse_args()
    claims = JsonClaimStore(args.data_dir).load_all_claims()
    report = run_validation_cycle(
        claims,
        stage=args.stage,
        seed=args.seed,
        iterations=args.iterations,
    )

    print(f"claims_used: {report.claims_used}")
    print(f"factors_touched: {', '.join(report.factors_touched) or '(none)'}")
    print(f"fair_value_median: {report.fair_value_median_usd / USD_TO_TRILLIONS:.2f}T USD")
    print(
        "fair_value_p10_p90: "
        f"{report.fair_value_p10_usd / USD_TO_TRILLIONS:.2f}T / "
        f"{report.fair_value_p90_usd / USD_TO_TRILLIONS:.2f}T USD"
    )
    print(f"samples: {len(report.fair_value_samples_usd)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the NVDA loaded-claim validation cycle."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory passed to JsonClaimStore.",
    )
    parser.add_argument(
        "--stage",
        choices=LIFECYCLE_STAGE_CHOICES,
        default=DEFAULT_STAGE,
        help="Lifecycle stage used for claim routing and Monte Carlo sampling.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_VALIDATION_SEED,
        help="Deterministic random seed.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_VALIDATION_ITERATIONS,
        help="Monte Carlo iteration count.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
