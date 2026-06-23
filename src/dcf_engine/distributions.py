"""Distribution parameter inversion and sampling."""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist
from typing import Final, Literal, Protocol, assert_never

from numpy.random import Generator

MIN_BETA_CONCENTRATION: Final = 0.1
LOW_SCALE_QUANTILE: Final = 0.10
HIGH_SCALE_QUANTILE: Final = 0.90


class ScaleSpecLike(Protocol):
    @property
    def center(self) -> float: ...

    @property
    def uncertainty(self) -> float: ...

type DistributionFamily = Literal[
    "beta",
    "student_t",
    "lognormal",
    "triangular",
    "normal",
    "truncnormal",
]


def beta_from_moments(mu: float, sigma: float) -> tuple[float, float]:
    _require_finite_positive("sigma", sigma)
    _require_probability("mu", mu)
    variance = min(sigma**2, mu * (1 - mu) * 0.99)
    concentration = max(mu * (1 - mu) / variance - 1, MIN_BETA_CONCENTRATION)
    # 경계 mu에서도 평균이 뒤집히지 않도록 shape ratio는 입력 mu를 그대로 따른다.
    return mu * concentration, (1 - mu) * concentration


def t_params_from_moments(mu: float, sigma: float, df: int = 7) -> tuple[float, float, int]:
    _require_finite("mu", mu)
    _require_finite_positive("sigma", sigma)
    if df <= 2:
        raise ValueError("df must be greater than 2")
    return mu, sigma * math.sqrt((df - 2) / df), df


def lognormal_from_moments(mean: float, std: float) -> tuple[float, float]:
    _require_finite_positive("mean", mean)
    _require_finite_positive("std", std)
    sigma_ln = math.sqrt(math.log(1 + (std / mean) ** 2))
    mu_ln = math.log(mean) - sigma_ln**2 / 2
    return mu_ln, sigma_ln


def lognormal_scale_from_median(median: float, std_in_orig_space: float) -> tuple[float, float]:
    _require_finite_positive("median", median)
    _require_finite_positive("std_in_orig_space", std_in_orig_space)
    sigma_ln = math.sqrt(math.log(1 + (std_in_orig_space / median) ** 2))
    return math.log(median), sigma_ln


def lognormal_scale_from_three_points(
    low: float, base: float, high: float
) -> tuple[float, float]:
    _require_finite_positive("low", low)
    _require_finite_positive("base", base)
    _require_finite_positive("high", high)
    if not low < base < high:
        raise ValueError("scale points must satisfy low < base < high")
    normal = NormalDist()
    quantile_width = normal.inv_cdf(HIGH_SCALE_QUANTILE) - normal.inv_cdf(
        LOW_SCALE_QUANTILE
    )
    # low/high의 log-space 폭으로 scale을 복원하고 base는 median으로 고정한다.
    sigma_ln = (math.log(high) - math.log(low)) / quantile_width
    return math.log(base), sigma_ln


def sample_scale(scale_spec: ScaleSpecLike, rng: Generator) -> float:
    _require_finite_positive("scale center", scale_spec.center)
    _require_finite("scale uncertainty", scale_spec.uncertainty)
    if scale_spec.uncertainty < 0:
        raise ValueError("scale uncertainty must not be negative")
    if scale_spec.uncertainty == 0:
        return scale_spec.center
    mu_ln, sigma_ln = lognormal_scale_from_median(
        scale_spec.center, scale_spec.uncertainty
    )
    return float(rng.lognormal(mu_ln, sigma_ln))


def triangular_from_mu(mu: float, low: float, high: float) -> tuple[float, float, float]:
    _require_finite("mu", mu)
    _require_finite("low", low)
    _require_finite("high", high)
    if low > high:
        raise ValueError("low must not exceed high")
    return low, min(max(mu, low), high), high


def params_from_moments(
    family: DistributionFamily,
    mu: float,
    sigma: float,
    *,
    low: float = 0.0,
    high: float = 1.0,
) -> tuple[float, ...]:
    match family:
        case "beta":
            return beta_from_moments(mu, sigma)
        case "student_t":
            loc, scale, df = t_params_from_moments(mu, sigma)
            return loc, scale, float(df)
        case "lognormal":
            return lognormal_from_moments(mu, sigma)
        case "triangular":
            return triangular_from_mu(mu, low, high)
        case "normal" | "truncnormal":
            return mu, sigma
        case _:
            assert_never(family)


def sample_distribution(
    family: DistributionFamily, params: Sequence[float], rng: Generator
) -> float:
    match family:
        case "beta":
            return float(rng.beta(params[0], params[1]))
        case "student_t":
            return float(params[0] + params[1] * rng.standard_t(int(params[2])))
        case "lognormal":
            return float(rng.lognormal(params[0], params[1]))
        case "triangular":
            return float(rng.triangular(params[0], params[1], params[2]))
        case "normal" | "truncnormal":
            return float(rng.normal(params[0], params[1]))
        case _:
            assert_never(family)


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _require_finite_positive(name: str, value: float) -> None:
    _require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_probability(name: str, value: float) -> None:
    _require_finite(name, value)
    if not 0 < value < 1:
        raise ValueError(f"{name} must be in (0, 1)")
