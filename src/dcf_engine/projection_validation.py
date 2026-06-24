"""Validation for scalar and vector going-concern projection inputs."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Final

import numpy as np
from numpy.typing import NDArray

from dcf_engine.assumption import REINVESTMENT_TOOL_BY_STAGE, ReinvestmentTool
from dcf_engine.lifecycle import LifecycleStage

COMMON_ASSUMPTION_SAMPLE_NAMES: Final[tuple[str, ...]] = (
    "REVENUE_CAGR",
    "OPERATING_MARGIN",
    "TAX_RATE",
    "WACC",
    "TERMINAL_GROWTH",
)
SAMPLE_NAME_BY_REINVESTMENT_TOOL: Final[dict[ReinvestmentTool, str]] = {
    "sales_to_capital": "SALES_TO_CAPITAL_RATIO",
    "roic": "ROIC",
}
MIN_RATE: Final = -1.0
MIN_REVENUE: Final = 0.0
MIN_TAX_RATE: Final = 0.0
MAX_TAX_RATE: Final = 1.0
MIN_FORECAST_YEARS: Final = 1


def validate_projection_inputs(
    *,
    initial_revenue: float,
    revenue_growth: float,
    operating_margin: float,
    tax_rate: float,
    wacc: float,
    terminal_growth: float,
    forecast_years: int,
    stage: LifecycleStage,
    sales_to_capital_ratio: float | None,
    roic: float | None,
) -> None:
    values = {
        "initial_revenue": initial_revenue,
        "revenue_growth": revenue_growth,
        "operating_margin": operating_margin,
        "tax_rate": tax_rate,
        "wacc": wacc,
        "terminal_growth": terminal_growth,
    }
    for name, value in values.items():
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")
    if initial_revenue < MIN_REVENUE:
        raise ValueError("initial_revenue must be nonnegative")
    if revenue_growth <= MIN_RATE:
        raise ValueError("revenue_growth must be greater than -1")
    if not MIN_TAX_RATE <= tax_rate <= MAX_TAX_RATE:
        raise ValueError("tax_rate must be between 0 and 1")
    validate_reinvestment_tool_value(
        stage=stage,
        sales_to_capital_ratio=sales_to_capital_ratio,
        roic=roic,
    )
    if wacc <= MIN_RATE:
        raise ValueError("wacc must be greater than -1")
    if terminal_growth <= MIN_RATE:
        raise ValueError("terminal_growth must be greater than -1")
    if wacc <= terminal_growth:
        raise ValueError("wacc must be greater than terminal_growth")
    if forecast_years < MIN_FORECAST_YEARS:
        raise ValueError("forecast_years must be positive")


def validated_samples(
    *,
    initial_revenue: float,
    assumption_samples: Mapping[str, NDArray[np.float64]],
    forecast_years: int,
    stage: LifecycleStage,
) -> dict[str, NDArray[np.float64]]:
    active_tool = active_reinvestment_sample_name(stage)
    sample_names = (*COMMON_ASSUMPTION_SAMPLE_NAMES, active_tool)
    missing = set(sample_names) - assumption_samples.keys()
    if missing:
        raise ValueError(f"missing assumption samples: {', '.join(sorted(missing))}")
    samples = {
        name: np.asarray(assumption_samples[name], dtype=np.float64)
        for name in sample_names
    }
    expected_shape = samples[COMMON_ASSUMPTION_SAMPLE_NAMES[0]].shape
    for name, values in samples.items():
        if values.shape != expected_shape:
            raise ValueError("assumption samples must have matching shapes")
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{name} samples must be finite")
    _validate_sample_ranges(initial_revenue, samples, forecast_years, active_tool)
    return samples


def active_reinvestment_sample_name(stage: LifecycleStage) -> str:
    return SAMPLE_NAME_BY_REINVESTMENT_TOOL[REINVESTMENT_TOOL_BY_STAGE[stage]]


def validate_reinvestment_tool_value(
    *,
    stage: LifecycleStage,
    sales_to_capital_ratio: float | None,
    roic: float | None,
) -> None:
    tool = REINVESTMENT_TOOL_BY_STAGE[stage]
    name = "sales_to_capital_ratio" if tool == "sales_to_capital" else "roic"
    value = sales_to_capital_ratio if tool == "sales_to_capital" else roic
    if value is None:
        raise ValueError(f"{name} is required for {stage} projections")
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= 0.0:
        raise ValueError(f"{name} must be positive")


def _validate_sample_ranges(
    initial_revenue: float,
    samples: Mapping[str, NDArray[np.float64]],
    forecast_years: int,
    active_tool: str,
) -> None:
    if not isfinite(initial_revenue) or initial_revenue < MIN_REVENUE:
        raise ValueError("initial_revenue must be finite and nonnegative")
    if forecast_years < MIN_FORECAST_YEARS:
        raise ValueError("forecast_years must be positive")
    if np.any(samples["REVENUE_CAGR"] <= MIN_RATE):
        raise ValueError("REVENUE_CAGR samples must be greater than -1")
    if np.any(
        (samples["TAX_RATE"] < MIN_TAX_RATE) | (samples["TAX_RATE"] > MAX_TAX_RATE)
    ):
        raise ValueError("TAX_RATE samples must be between 0 and 1")
    if np.any(samples[active_tool] <= 0.0):
        raise ValueError(f"{active_tool} samples must be positive")
    if np.any(samples["WACC"] <= MIN_RATE):
        raise ValueError("WACC samples must be greater than -1")
    if np.any(samples["TERMINAL_GROWTH"] <= MIN_RATE):
        raise ValueError("TERMINAL_GROWTH samples must be greater than -1")
    if np.any(samples["WACC"] <= samples["TERMINAL_GROWTH"]):
        raise ValueError("WACC samples must be greater than TERMINAL_GROWTH samples")
