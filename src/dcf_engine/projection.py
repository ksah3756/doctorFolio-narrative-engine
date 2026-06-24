"""Pure operating projection from sampled assumptions to going-concern value."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

import numpy as np
from numpy.typing import NDArray

from dcf_engine import projection_validation
from dcf_engine.assumption import REINVESTMENT_TOOL_BY_STAGE, compute_reinvestment
from dcf_engine.lifecycle import LifecycleStage


@dataclass(frozen=True)
class ProjectionInputs:
    initial_revenue: float
    revenue_growth: float
    operating_margin: float
    tax_rate: float
    wacc: float
    terminal_growth: float
    forecast_years: int
    sales_to_capital_ratio: float | None = None
    roic: float | None = None
    stage: LifecycleStage = "growth"

    def __post_init__(self) -> None:
        projection_validation.validate_projection_inputs(
            initial_revenue=self.initial_revenue,
            revenue_growth=self.revenue_growth,
            operating_margin=self.operating_margin,
            tax_rate=self.tax_rate,
            wacc=self.wacc,
            terminal_growth=self.terminal_growth,
            forecast_years=self.forecast_years,
            stage=self.stage,
            sales_to_capital_ratio=self.sales_to_capital_ratio,
            roic=self.roic,
        )

    @property
    def reinvestment_tool_value(self) -> float:
        tool = REINVESTMENT_TOOL_BY_STAGE[self.stage]
        value = self.sales_to_capital_ratio if tool == "sales_to_capital" else self.roic
        assert value is not None
        return value


@dataclass(frozen=True)
class ProjectionResult:
    yearly_revenue: NDArray[np.float64]
    yearly_reinvestment: NDArray[np.float64]
    yearly_fcff: NDArray[np.float64]
    discounted_fcff: NDArray[np.float64]
    terminal_reinvestment: float
    terminal_fcff: float
    terminal_value: float
    discounted_terminal_value: float
    going_concern_firm_value: float


def project_going_concern(inputs: ProjectionInputs) -> ProjectionResult:
    yearly_revenue = np.empty(inputs.forecast_years, dtype=np.float64)
    yearly_reinvestment = np.empty(inputs.forecast_years, dtype=np.float64)
    yearly_fcff = np.empty(inputs.forecast_years, dtype=np.float64)
    discounted_fcff = np.empty(inputs.forecast_years, dtype=np.float64)
    previous_revenue = inputs.initial_revenue
    for index in range(inputs.forecast_years):
        revenue = previous_revenue * (1.0 + inputs.revenue_growth)
        fcff, reinvestment = _scalar_fcff(
            inputs.stage,
            previous_revenue,
            revenue,
            inputs.operating_margin,
            inputs.tax_rate,
            inputs.revenue_growth,
            inputs.reinvestment_tool_value,
        )
        yearly_revenue[index] = revenue
        yearly_reinvestment[index] = reinvestment
        yearly_fcff[index] = fcff
        discounted_fcff[index] = fcff / (1.0 + inputs.wacc) ** (index + 1)
        previous_revenue = revenue

    terminal_revenue = previous_revenue * (1.0 + inputs.terminal_growth)
    terminal_fcff, terminal_reinvestment = _scalar_fcff(
        inputs.stage,
        previous_revenue,
        terminal_revenue,
        inputs.operating_margin,
        inputs.tax_rate,
        inputs.terminal_growth,
        inputs.reinvestment_tool_value,
    )
    terminal_value = terminal_fcff / (inputs.wacc - inputs.terminal_growth)
    discounted_terminal_value = terminal_value / (1.0 + inputs.wacc) ** inputs.forecast_years
    firm_value = float(discounted_fcff.sum() + discounted_terminal_value)
    if not isfinite(firm_value):
        raise ValueError("going_concern_firm_value must be finite")
    return ProjectionResult(
        yearly_revenue=yearly_revenue,
        yearly_reinvestment=yearly_reinvestment,
        yearly_fcff=yearly_fcff,
        discounted_fcff=discounted_fcff,
        terminal_reinvestment=terminal_reinvestment,
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
    stage: LifecycleStage = "growth",
) -> NDArray[np.float64]:
    samples = projection_validation.validated_samples(
        initial_revenue=initial_revenue,
        assumption_samples=assumption_samples,
        forecast_years=forecast_years,
        stage=stage,
    )
    growth = samples["REVENUE_CAGR"]
    wacc = samples["WACC"]
    terminal_growth = samples["TERMINAL_GROWTH"]

    revenue = np.full(growth.shape, initial_revenue, dtype=np.float64)
    operating_value = np.zeros(growth.shape, dtype=np.float64)
    for year in range(1, forecast_years + 1):
        next_revenue = revenue * (1.0 + growth)
        yearly_fcff = _sample_fcff(
            stage,
            revenue,
            next_revenue,
            growth,
            samples,
        )
        operating_value += yearly_fcff / (1.0 + wacc) ** year
        revenue = next_revenue

    terminal_revenue = revenue * (1.0 + terminal_growth)
    terminal_fcff = _sample_fcff(
        stage,
        revenue,
        terminal_revenue,
        terminal_growth,
        samples,
    )
    terminal_value = terminal_fcff / (wacc - terminal_growth)
    values = operating_value + terminal_value / (1.0 + wacc) ** forecast_years
    if not np.all(np.isfinite(values)):
        raise ValueError("going_concern_firm_value samples must be finite")
    return np.asarray(values, dtype=np.float64)


def _scalar_fcff(
    stage: LifecycleStage,
    previous_revenue: float,
    revenue: float,
    operating_margin: float,
    tax_rate: float,
    growth: float,
    reinvestment_tool: float,
) -> tuple[float, float]:
    nopat = revenue * operating_margin * (1.0 - tax_rate)
    reinvestment = compute_reinvestment(
        stage,
        delta_revenue=revenue - previous_revenue,
        nopat=nopat,
        growth=growth,
        tool_value=reinvestment_tool,
    )
    return nopat - reinvestment, reinvestment


def _sample_fcff(
    stage: LifecycleStage,
    previous_revenue: NDArray[np.float64],
    revenue: NDArray[np.float64],
    growth: NDArray[np.float64],
    samples: Mapping[str, NDArray[np.float64]],
) -> NDArray[np.float64]:
    nopat = revenue * samples["OPERATING_MARGIN"] * (1.0 - samples["TAX_RATE"])
    reinvestment = compute_reinvestment(
        stage,
        delta_revenue=revenue - previous_revenue,
        nopat=nopat,
        growth=growth,
        tool_value=samples[projection_validation.active_reinvestment_sample_name(stage)],
    )
    return np.asarray(nopat - reinvestment, dtype=np.float64)
