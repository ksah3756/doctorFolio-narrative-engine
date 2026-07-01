"""Replay-only Type-1 calibration fixture specs."""

from __future__ import annotations

from typing import Final

type PullSpec = tuple[str, str, float, float]
type ScenarioSpec = tuple[str, tuple[PullSpec, ...]]

DEFAULT_TYPE1_SCENARIO_SPECS: Final[tuple[ScenarioSpec, ...]] = (
    (
        "stable-bipolar",
        (
            ("positive-1", "margin", 1.0, 1.0),
            ("positive-2", "margin", 1.0, 1.0),
            ("positive-3", "margin", 1.0, 1.0),
            ("negative-1", "margin", -1.0, 1.0),
            ("negative-2", "margin", -1.0, 1.0),
        ),
    ),
    (
        "unanimous-one-sided",
        (
            ("positive-1", "margin", 1.0, 1.0),
            ("positive-2", "margin", 1.0, 1.0),
            ("positive-3", "margin", 1.0, 1.0),
        ),
    ),
    (
        "unstable-two-claim",
        (
            ("positive", "margin", 1.0, 1.0),
            ("negative", "margin", -1.0, 1.0),
        ),
    ),
    (
        "thin-outlier-driven",
        (
            ("majority-1", "revenue_cagr", 1.0, 1.0),
            ("majority-2", "revenue_cagr", 1.1, 1.0),
            ("majority-3", "revenue_cagr", 0.9, 1.0),
            ("thin-outlier", "revenue_cagr", -20.0, 0.10),
        ),
    ),
)
