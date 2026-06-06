"""Numerical sanity checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from dcf_engine.lifecycle import LifecycleStage

INDUSTRY_MAX_ROIC: Final[float] = 0.50


def implied_roic(sampled: Mapping[str, float]) -> float:
    margin = sampled["OPERATING_MARGIN"]
    tax_rate = sampled["TAX_RATE"]
    sales_to_capital = sampled["SALES_TO_CAPITAL_RATIO"]
    return margin * (1 - tax_rate) * sales_to_capital


def passes_imputed_roic_check(stage: LifecycleStage, sampled: Mapping[str, float]) -> bool:
    if stage not in ("young", "growth"):
        return True
    required = {"OPERATING_MARGIN", "TAX_RATE", "SALES_TO_CAPITAL_RATIO"}
    if not required.issubset(sampled):
        return True
    return implied_roic(sampled) <= INDUSTRY_MAX_ROIC * 3
