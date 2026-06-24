"""Pure operating projection from sampled assumptions to going-concern value."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Final, overload

import numpy as np
from numpy.typing import NDArray

ASSUMPTION_SAMPLE_NAMES: Final[tuple[str, ...]] = (
    "REVENUE_CAGR",
    "OPERATING_MARGIN",
    "TAX_RATE",
    "SALES_TO_CAPITAL_RATIO",
    "WACC",
    "TERMINAL_GROWTH",
)
MIN_RATE: Final = -1.0
MIN_REVENUE: Final = 0.0
MIN_TAX_RATE: Final = 0.0
MAX_TAX_RATE: Final = 1.0
MIN_FORECAST_YEARS: Final = 1


@dataclass(frozen=True)
class ProjectionInputs:
    initial_revenue: float
    revenue_growth: float
    operating_margin: float
    tax_rate: float
    sales_to_capital_ratio: float
    wacc: float
    terminal_growth: float
    forecast_years: int

    def __post_init__(self) -> None:
        values = {
            "initial_revenue": self.initial_revenue,
            "revenue_growth": self.revenue_growth,
            "operating_margin": self.operating_margin,
            "tax_rate": self.tax_rate,
            "sales_to_capital_ratio": self.sales_to_capital_ratio,
            "wacc": self.wacc,
            "terminal_growth": self.terminal_growth,
        }
        for name, value in values.items():
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        _validate_projection_ranges(
            initial_revenue=self.initial_revenue,
            revenue_growth=self.revenue_growth,
            tax_rate=self.tax_rate,
            sales_to_capital_ratio=self.sales_to_capital_ratio,
            wacc=self.wacc,
            terminal_growth=self.terminal_growth,
            forecast_years=self.forecast_years,
        )


@dataclass(frozen=True)
class ProjectionResult:
    yearly_revenue: NDArray[np.float64]
    yearly_fcff: NDArray[np.float64]
    discounted_fcff: NDArray[np.float64]
    terminal_fcff: float
    terminal_value: float
    discounted_terminal_value: float
    going_concern_firm_value: float


def project_going_concern(inputs: ProjectionInputs) -> ProjectionResult:
    yearly_revenue = np.empty(inputs.forecast_years, dtype=np.float64)
    yearly_fcff = np.empty(inputs.forecast_years, dtype=np.float64)
    discounted_fcff = np.empty(inputs.forecast_years, dtype=np.float64)
    previous_revenue = inputs.initial_revenue
    for index in range(inputs.forecast_years):
        revenue = previous_revenue * (1.0 + inputs.revenue_growth)
        fcff = _fcff(
            previous_revenue,
            revenue,
            inputs.operating_margin,
            inputs.tax_rate,
            inputs.sales_to_capital_ratio,
        )
        yearly_revenue[index] = revenue
        yearly_fcff[index] = fcff
        discounted_fcff[index] = fcff / (1.0 + inputs.wacc) ** (index + 1)
        previous_revenue = revenue

    terminal_revenue = previous_revenue * (1.0 + inputs.terminal_growth)
    terminal_fcff = _fcff(
        previous_revenue,
        terminal_revenue,
        inputs.operating_margin,
        inputs.tax_rate,
        inputs.sales_to_capital_ratio,
    )
    terminal_value = terminal_fcff / (inputs.wacc - inputs.terminal_growth)
    discounted_terminal_value = terminal_value / (1.0 + inputs.wacc) ** inputs.forecast_years
    firm_value = float(discounted_fcff.sum() + discounted_terminal_value)
    if not isfinite(firm_value):
        raise ValueError("going_concern_firm_value must be finite")
    return ProjectionResult(
        yearly_revenue=yearly_revenue,
        yearly_fcff=yearly_fcff,
        discounted_fcff=discounted_fcff,
        terminal_fcff=terminal_fcff,
        terminal_value=terminal_value,
        discounted_terminal_value=discounted_terminal_value,
        going_concern_firm_value=firm_value,
    )


def going_concern_value_samples(
    *,
    initial_revenue: float,
    assumption_samples: Mapping[str, NDArray[np.float64]],
    forecast_years: int,
) -> NDArray[np.float64]:
    samples = _validated_samples(assumption_samples)
    _validate_sample_ranges(initial_revenue, samples, forecast_years)
    growth = samples["REVENUE_CAGR"]
    margin = samples["OPERATING_MARGIN"]
    tax_rate = samples["TAX_RATE"]
    sales_to_capital = samples["SALES_TO_CAPITAL_RATIO"]
    wacc = samples["WACC"]
    terminal_growth = samples["TERMINAL_GROWTH"]

    revenue = np.full(growth.shape, initial_revenue, dtype=np.float64)
    operating_value = np.zeros(growth.shape, dtype=np.float64)
    for year in range(1, forecast_years + 1):
        next_revenue = revenue * (1.0 + growth)
        yearly_fcff = _fcff(revenue, next_revenue, margin, tax_rate, sales_to_capital)
        operating_value += yearly_fcff / (1.0 + wacc) ** year
        revenue = next_revenue

    terminal_revenue = revenue * (1.0 + terminal_growth)
    terminal_fcff = _fcff(revenue, terminal_revenue, margin, tax_rate, sales_to_capital)
    terminal_value = terminal_fcff / (wacc - terminal_growth)
    values = operating_value + terminal_value / (1.0 + wacc) ** forecast_years
    if not np.all(np.isfinite(values)):
        raise ValueError("going_concern_firm_value samples must be finite")
    return np.asarray(values, dtype=np.float64)


@overload
def _fcff(
    previous_revenue: float,
    revenue: float,
    operating_margin: float,
    tax_rate: float,
    sales_to_capital_ratio: float,
) -> float: ...


@overload
def _fcff(
    previous_revenue: NDArray[np.float64],
    revenue: NDArray[np.float64],
    operating_margin: NDArray[np.float64],
    tax_rate: NDArray[np.float64],
    sales_to_capital_ratio: NDArray[np.float64],
) -> NDArray[np.float64]: ...


def _fcff(
    previous_revenue: float | NDArray[np.float64],
    revenue: float | NDArray[np.float64],
    operating_margin: float | NDArray[np.float64],
    tax_rate: float | NDArray[np.float64],
    sales_to_capital_ratio: float | NDArray[np.float64],
) -> float | NDArray[np.float64]:
    nopat = revenue * operating_margin * (1.0 - tax_rate)
    reinvestment = (revenue - previous_revenue) / sales_to_capital_ratio
    return nopat - reinvestment


def _validated_samples(
    assumption_samples: Mapping[str, NDArray[np.float64]],
) -> dict[str, NDArray[np.float64]]:
    missing = set(ASSUMPTION_SAMPLE_NAMES) - assumption_samples.keys()
    if missing:
        raise ValueError(f"missing assumption samples: {', '.join(sorted(missing))}")
    samples = {
        name: np.asarray(assumption_samples[name], dtype=np.float64)
        for name in ASSUMPTION_SAMPLE_NAMES
    }
    expected_shape = samples[ASSUMPTION_SAMPLE_NAMES[0]].shape
    for name, values in samples.items():
        if values.shape != expected_shape:
            raise ValueError("assumption samples must have matching shapes")
        if not np.all(np.isfinite(values)):
            raise ValueError(f"{name} samples must be finite")
    return samples


def _validate_projection_ranges(
    *,
    initial_revenue: float,
    revenue_growth: float,
    tax_rate: float,
    sales_to_capital_ratio: float,
    wacc: float,
    terminal_growth: float,
    forecast_years: int,
) -> None:
    if initial_revenue < MIN_REVENUE:
        raise ValueError("initial_revenue must be nonnegative")
    if revenue_growth <= MIN_RATE:
        raise ValueError("revenue_growth must be greater than -1")
    if not MIN_TAX_RATE <= tax_rate <= MAX_TAX_RATE:
        raise ValueError("tax_rate must be between 0 and 1")
    if sales_to_capital_ratio <= 0.0:
        raise ValueError("sales_to_capital_ratio must be positive")
    if wacc <= MIN_RATE:
        raise ValueError("wacc must be greater than -1")
    if terminal_growth <= MIN_RATE:
        raise ValueError("terminal_growth must be greater than -1")
    if wacc <= terminal_growth:
        raise ValueError("wacc must be greater than terminal_growth")
    if forecast_years < MIN_FORECAST_YEARS:
        raise ValueError("forecast_years must be positive")


def _validate_sample_ranges(
    initial_revenue: float,
    samples: Mapping[str, NDArray[np.float64]],
    forecast_years: int,
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
    if np.any(samples["SALES_TO_CAPITAL_RATIO"] <= 0.0):
        raise ValueError("SALES_TO_CAPITAL_RATIO samples must be positive")
    if np.any(samples["WACC"] <= MIN_RATE):
        raise ValueError("WACC samples must be greater than -1")
    if np.any(samples["TERMINAL_GROWTH"] <= MIN_RATE):
        raise ValueError("TERMINAL_GROWTH samples must be greater than -1")
    if np.any(samples["WACC"] <= samples["TERMINAL_GROWTH"]):
        raise ValueError("WACC samples must be greater than TERMINAL_GROWTH samples")
