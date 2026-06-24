"""Pure operating projection from sampled assumptions to going-concern value."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import overload

import numpy as np
from numpy.typing import NDArray

from dcf_engine.projection_validation import (
    validate_projection_inputs,
    validated_samples,
)


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
        validate_projection_inputs(
            initial_revenue=self.initial_revenue,
            revenue_growth=self.revenue_growth,
            operating_margin=self.operating_margin,
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
    samples = validated_samples(
        initial_revenue=initial_revenue,
        assumption_samples=assumption_samples,
        forecast_years=forecast_years,
    )
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
