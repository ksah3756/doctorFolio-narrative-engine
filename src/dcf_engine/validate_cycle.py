"""Loaded-claim validation cycle wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from dcf_engine.claim import Claim
from dcf_engine.factor import Regime
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.monte_carlo import MonteCarloConfig, mc_run
from dcf_engine.nvda_spike import _assumptions, _company, _fair_values, sample_tam_total
from dcf_engine.routing import route_claims_to_factors

DEFAULT_VALIDATION_SEED: Final[int] = 20260629
DEFAULT_VALIDATION_ITERATIONS: Final[int] = 1_000
VALIDATION_REGIME: Final[Regime] = "normal"
P10_PERCENTILE: Final[float] = 10.0
P90_PERCENTILE: Final[float] = 90.0
MIN_ITERATIONS: Final[int] = 1


@dataclass(frozen=True)
class ValidationReport:
    fair_value_median_usd: float
    fair_value_p10_usd: float
    fair_value_p90_usd: float
    fair_value_samples_usd: NDArray[np.float64]
    claims_used: int
    factors_touched: tuple[str, ...]


def run_validation_cycle(
    claims: list[Claim],
    *,
    stage: LifecycleStage,
    seed: int = DEFAULT_VALIDATION_SEED,
    iterations: int = DEFAULT_VALIDATION_ITERATIONS,
) -> ValidationReport:
    if not claims:
        raise ValueError("claims must not be empty")
    if iterations < MIN_ITERATIONS:
        raise ValueError("iterations must be positive")

    rng = np.random.default_rng(seed)
    tam_samples = np.array([sample_tam_total(rng) for _ in range(iterations)], dtype=np.float64)
    factors = route_claims_to_factors(list(claims), stage)
    mc_result = mc_run(
        factors,
        _assumptions(tam_mu=float(tam_samples.mean())),
        stage,
        VALIDATION_REGIME,
        _company(),
        MonteCarloConfig(iterations=iterations, seed=seed),
    )
    if len(mc_result.accepted_indices) == 0:
        raise RuntimeError("validation cycle produced no accepted valuation samples")

    fair_values = _fair_values(tam_samples[mc_result.accepted_indices], mc_result.samples)
    return ValidationReport(
        fair_value_median_usd=float(np.median(fair_values)),
        fair_value_p10_usd=float(np.percentile(fair_values, P10_PERCENTILE)),
        fair_value_p90_usd=float(np.percentile(fair_values, P90_PERCENTILE)),
        fair_value_samples_usd=fair_values,
        claims_used=len(claims),
        factors_touched=tuple(factors),
    )
