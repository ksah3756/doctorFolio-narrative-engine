"""Monte Carlo sampling engine."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from numpy.random import Generator

from dcf_engine.assumption import AssumptionState
from dcf_engine.distributions import params_from_moments, sample_distribution
from dcf_engine.factor import FactorState, Regime
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.loading import apply_constraints
from dcf_engine.validation import passes_imputed_roic_check


@dataclass(frozen=True)
class MonteCarloConfig:
    iterations: int = 1_000
    seed: int = 0
    max_resample: int = 5
    now: datetime = datetime(2026, 6, 1, tzinfo=UTC)
    t_year: float = 1.0


@dataclass(frozen=True)
class MonteCarloResult:
    samples: dict[str, np.ndarray]
    reject_rate: float


def mc_iteration_with_validation(
    *,
    factor_states: Mapping[str, FactorState],
    assumptions: list[AssumptionState],
    stage: LifecycleStage,
    regime: Regime,
    company: Mapping[str, float],
    config: MonteCarloConfig,
) -> dict[str, float] | None:
    rng = np.random.default_rng(config.seed)
    for _ in range(config.max_resample):
        sampled = mc_iteration(
            factor_states=factor_states,
            assumptions=assumptions,
            stage=stage,
            regime=regime,
            company=company,
            rng=rng,
        )
        if passes_imputed_roic_check(stage, sampled):
            return sampled
    return None


def mc_run(
    factor_states: Mapping[str, FactorState],
    assumptions: list[AssumptionState],
    stage: LifecycleStage,
    regime: Regime,
    company: Mapping[str, float],
    config: MonteCarloConfig,
) -> MonteCarloResult:
    rng = np.random.default_rng(config.seed)
    accepted: list[dict[str, float]] = []
    dropped = 0
    for _ in range(config.iterations):
        # 비현실적인 재무 조합은 버려 reject_rate를 calibration 신호로 남긴다.
        sampled = _iteration_with_rng(
            factor_states=factor_states,
            assumptions=assumptions,
            stage=stage,
            regime=regime,
            company=company,
            rng=rng,
            max_resample=config.max_resample,
        )
        if sampled is None:
            dropped += 1
        else:
            accepted.append(sampled)
    reject_rate = dropped / config.iterations
    if reject_rate > 0.30:
        warnings.warn(
            f"High reject rate {reject_rate:.2%}: base_mu may need recalibration",
            RuntimeWarning,
            stacklevel=2,
        )
    return MonteCarloResult(samples=_transpose_samples(accepted), reject_rate=reject_rate)


def mc_iteration(
    *,
    factor_states: Mapping[str, FactorState],
    assumptions: list[AssumptionState],
    stage: LifecycleStage,
    regime: Regime,
    company: Mapping[str, float],
    rng: Generator,
) -> dict[str, float]:
    # factor를 먼저 흔들어 narrative 불확실성이 assumption 분포로 전달되게 한다.
    sampled_factors = _sample_factors(factor_states, stage, regime, rng)
    out: dict[str, float] = {}
    for assumption in assumptions:
        if not assumption.active:
            continue
        mu = _shifted_mu(assumption, sampled_factors)
        mu = apply_constraints(mu, assumption, company)
        params = params_from_moments(
            assumption.distribution_family,
            mu,
            assumption.current_sigma,
            low=assumption.constraints.get("low", 0.0),
            high=assumption.constraints.get("high", 1.0),
        )
        out[assumption.name] = sample_distribution(assumption.distribution_family, params, rng)
    return out


def _iteration_with_rng(
    *,
    factor_states: Mapping[str, FactorState],
    assumptions: list[AssumptionState],
    stage: LifecycleStage,
    regime: Regime,
    company: Mapping[str, float],
    rng: Generator,
    max_resample: int,
) -> dict[str, float] | None:
    for _ in range(max_resample):
        sampled = mc_iteration(
            factor_states=factor_states,
            assumptions=assumptions,
            stage=stage,
            regime=regime,
            company=company,
            rng=rng,
        )
        if passes_imputed_roic_check(stage, sampled):
            return sampled
    return None


def _sample_factors(
    factor_states: Mapping[str, FactorState],
    stage: LifecycleStage,
    regime: Regime,
    rng: Generator,
) -> dict[str, float]:
    base_uncertainty = {"young": 0.8, "growth": 0.5, "mature": 0.3, "decline": 0.5}[stage]
    regime_mult = {"normal": 1.0, "stress": 1.5, "boom": 1.1}[regime]
    return {
        name: float(rng.normal(state.current_value, base_uncertainty * regime_mult))
        for name, state in factor_states.items()
    }


def _shifted_mu(assumption: AssumptionState, sampled_factors: Mapping[str, float]) -> float:
    loading = {
        "REVENUE_CAGR": {"DemandStrength": 0.7, "CompetitiveAdvantage": 0.4, "MacroCondition": 0.2},
        "OPERATING_MARGIN": {
            "DemandStrength": 0.2,
            "CompetitiveAdvantage": 0.5,
            "OperatingEfficiency": 0.6,
            "MacroCondition": 0.1,
        },
        "SALES_TO_CAPITAL_RATIO": {"OperatingEfficiency": 0.5},
        "WACC": {"MacroCondition": -0.7},
        "DEFAULT_PROBABILITY": {"MacroCondition": -0.2},
        "MARKET_SHARE": {"DemandStrength": 0.1, "CompetitiveAdvantage": 0.8},
    }.get(assumption.name, {})
    mu_shift = sum(
        loading[name] * sampled_factors[name] for name in loading if name in sampled_factors
    )
    return assumption.base_mu + mu_shift * assumption.shift_scale.center


def _transpose_samples(samples: list[dict[str, float]]) -> dict[str, np.ndarray]:
    if not samples:
        return {}
    names = samples[0].keys()
    return {name: np.array([sample[name] for sample in samples], dtype=float) for name in names}
