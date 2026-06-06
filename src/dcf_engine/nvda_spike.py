"""NVDA-only MVP spike pipeline."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import numpy as np

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.bridge import BridgeInputs, equity_value
from dcf_engine.claim import (
    Claim,
    ClaimDirection,
    ClaimNature,
    ClaimSubject,
    ExtractionQuality,
    MagnitudeQualifier,
    SourceRef,
)
from dcf_engine.distributions import DistributionFamily
from dcf_engine.monte_carlo import MonteCarloConfig, mc_run
from dcf_engine.routing import route_claims_to_factors


@dataclass(frozen=True)
class NvdaSpikeResult:
    tam_mean_usd: float
    fair_value_median_usd: float
    fair_value_p10_usd: float
    fair_value_p90_usd: float
    reject_rate: float
    revenue_cagr_baseline_mu: float
    revenue_cagr_with_demand_claim_mu: float
    margin_baseline_mu: float
    margin_with_cost_claim_mu: float
    fair_value_samples_usd: np.ndarray


def run_nvda_spike(*, seed: int = 20260603, iterations: int = 1_000) -> NvdaSpikeResult:
    rng = np.random.default_rng(seed)
    # TAM에서 equity value까지 한 번에 검증되는 deterministic 세로 슬라이스로 유지한다.
    tam_samples = np.array([sample_tam_total(rng) for _ in range(iterations)], dtype=float)
    factors = route_claims_to_factors(_nvda_claims(), "growth")
    baseline_factors = route_claims_to_factors([], "growth")
    company = _company()
    assumptions = _assumptions(tam_mu=float(tam_samples.mean()))
    mc_result = mc_run(
        factors,
        assumptions,
        "growth",
        "normal",
        company,
        MonteCarloConfig(iterations=iterations, seed=seed),
    )
    fair_values = _fair_values(
        tam_samples[: len(mc_result.samples["REVENUE_CAGR"])], mc_result.samples
    )
    # 라우팅 부호 의도가 end-to-end 테스트에서 바로 드러나도록 비교값을 함께 반환한다.
    demand_only = route_claims_to_factors([_data_center_growth_claim()], "growth")
    cost_only = route_claims_to_factors([_cost_claim()], "growth")
    revenue_base = _shift_mu("REVENUE_CAGR", baseline_factors)
    revenue_demand = _shift_mu("REVENUE_CAGR", demand_only)
    margin_base = _shift_mu("OPERATING_MARGIN", baseline_factors)
    margin_cost = _shift_mu("OPERATING_MARGIN", cost_only)
    return NvdaSpikeResult(
        tam_mean_usd=float(tam_samples.mean()),
        fair_value_median_usd=float(np.median(fair_values)),
        fair_value_p10_usd=float(np.percentile(fair_values, 10)),
        fair_value_p90_usd=float(np.percentile(fair_values, 90)),
        reject_rate=mc_result.reject_rate,
        revenue_cagr_baseline_mu=revenue_base,
        revenue_cagr_with_demand_claim_mu=revenue_demand,
        margin_baseline_mu=margin_base,
        margin_with_cost_claim_mu=margin_cost,
        fair_value_samples_usd=fair_values,
    )


def sample_tam_total(rng: np.random.Generator) -> float:
    data = _load_multiplicands()
    # 단일 TAM 상수에 묶지 않고 multiplicand 기반 시장 샘플로 불확실성을 보존한다.
    data_center = _sample_data_center(data.data_center, rng)
    gaming_section = data.gaming
    pro_viz_section = data.pro_viz
    auto_section = data.auto
    gaming = (
        _sample_spec(gaming_section["pc_gamers_millions"], rng)
        * 1_000_000
        / _sample_spec(gaming_section["upgrade_cycle_years"], rng)
        * _sample_spec(gaming_section["asp_usd"], rng)
        * _sample_spec(gaming_section["nvda_share"], rng)
    )
    pro_viz = _sample_spec(pro_viz_section["workstation_market_usd"], rng) * _sample_spec(
        pro_viz_section["nvda_share"], rng
    )
    auto = (
        _sample_spec(auto_section["vehicles_millions"], rng)
        * 1_000_000
        * _sample_spec(auto_section["chip_content_per_vehicle_usd"], rng)
        * _sample_spec(auto_section["nvda_share"], rng)
    )
    return data_center + gaming + pro_viz + auto


type TomlSpec = Mapping[str, float | str]
type TomlSection = Mapping[str, Mapping[str, TomlSpec]]


@dataclass(frozen=True)
class Multiplicands:
    data_center: TomlSection
    gaming: Mapping[str, TomlSpec]
    pro_viz: Mapping[str, TomlSpec]
    auto: Mapping[str, TomlSpec]


def _sample_data_center(section: TomlSection, rng: np.random.Generator) -> float:
    hyperscaler = section["hyperscaler"]
    enterprise = section["enterprise"]
    hyper_tam = (
        _sample_spec(hyperscaler["count"], rng)
        * _sample_spec(hyperscaler["ai_capex_per_year_usd"], rng)
        * _sample_spec(hyperscaler["nvda_addressable_share"], rng)
    )
    enterprise_tam = _sample_spec(enterprise["total_market_usd"], rng) * _sample_spec(
        enterprise["nvda_share"], rng
    )
    return hyper_tam + enterprise_tam


def _sample_spec(spec: Mapping[str, float | str], rng: np.random.Generator) -> float:
    center = float(spec["center"])
    uncertainty = float(spec["uncertainty"])
    distribution = str(spec["distribution"])
    if distribution == "beta":
        variance = min(uncertainty**2, center * (1 - center) * 0.99)
        concentration = center * (1 - center) / variance - 1
        return float(rng.beta(center * concentration, (1 - center) * concentration))
    sigma_ln = np.sqrt(np.log(1 + (uncertainty / center) ** 2))
    mu_ln = np.log(center) - sigma_ln**2 / 2
    return float(rng.lognormal(mu_ln, sigma_ln))


def _fair_values(tam_samples: np.ndarray, samples: Mapping[str, np.ndarray]) -> np.ndarray:
    # full DCF가 아니라 spike용 bridge이므로 가치 분포가 이어지는 경로를 우선 보존한다.
    revenue = tam_samples * samples["MARKET_SHARE"]
    terminal_margin = np.maximum(samples["OPERATING_MARGIN"], 0.05)
    fcff = revenue * terminal_margin * (1 - samples["TAX_RATE"]) * 1.70
    discount_spread = np.maximum(samples["WACC"] - 0.035, 0.025)
    going_concern = fcff / discount_spread
    values = [
        equity_value(
            BridgeInputs(
                going_concern_firm_value=float(gc),
                liquidation_firm_value=float(gc * 0.25),
                default_probability=float(default_probability),
                interest_bearing_debt=11_000_000_000,
                lease_liability=3_000_000_000,
                minority_interest=0.0,
                cash_and_non_operating_assets=31_000_000_000,
                option_value=6_000_000_000,
            )
        )
        for gc, default_probability in zip(
            going_concern, samples["DEFAULT_PROBABILITY"], strict=True
        )
    ]
    return np.array(values, dtype=float)


def _assumptions(*, tam_mu: float) -> list[AssumptionState]:
    # growth 단계 NVDA에서는 ROIC를 비활성화하고 imputed ROIC는 sanity check로만 쓴다.
    return [
        _assumption("TAM", "normal", tam_mu, tam_mu * 0.15, tam_mu, tam_mu * 0.15, False),
        _assumption("MARKET_SHARE", "beta", 0.62, 0.05, 0.62, 0.05, True),
        _assumption("REVENUE_CAGR", "student_t", 0.26, 0.08, 0.26, 0.08, True),
        _assumption("OPERATING_MARGIN", "normal", 0.56, 0.05, 0.56, 0.05, True),
        _assumption("TAX_RATE", "normal", 0.13, 0.015, 0.13, 0.015, True),
        _assumption("SALES_TO_CAPITAL_RATIO", "lognormal", 2.4, 0.25, 2.4, 0.25, True),
        _assumption("WACC", "normal", 0.095, 0.012, 0.095, 0.012, True),
        _assumption("DEFAULT_PROBABILITY", "beta", 0.015, 0.008, 0.015, 0.008, True),
    ]


def _assumption(
    name: str,
    family: DistributionFamily,
    mu: float,
    sigma: float,
    base_mu: float,
    base_sigma: float,
    active: bool,
) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=mu,
        current_sigma=sigma,
        base_mu=base_mu,
        base_sigma=base_sigma,
        shift_scale=ScaleSpec(center=0.05, uncertainty=0.0),
        constraints={"low": 0.0, "high": 1.0},
        active=active,
    )


def _shift_mu(name: str, factors: Mapping[str, object]) -> float:
    assumption = next(asm for asm in _assumptions(tam_mu=302_000_000_000) if asm.name == name)
    if name == "REVENUE_CAGR":
        loading = {"DemandStrength": 0.7, "CompetitiveAdvantage": 0.4}
    else:
        loading = {"DemandStrength": 0.2, "CompetitiveAdvantage": 0.5, "OperatingEfficiency": 0.6}
    shift = 0.0
    for factor_name, factor in factors.items():
        if factor_name in loading and hasattr(factor, "current_value"):
            shift += loading[factor_name] * float(factor.current_value)
    return assumption.base_mu + shift * assumption.shift_scale.center


def _nvda_claims() -> list[Claim]:
    # ingestion/LLM은 제외하고 hardcoded claim으로 엔진 의미론만 검증한다.
    return [
        _data_center_growth_claim(),
        _claim("cost-pressure", "COST_SIGNAL", "INCREASE", "MODERATE", "EXTERNAL"),
        _claim("blackwell-demand", "DEMAND_SIGNAL", "INCREASE", "STRONG", "GUIDANCE"),
        _claim("cuda-moat", "COMPETITIVE_POSITION", "INCREASE", "STRONG", "STRUCTURAL"),
        _claim("hyperscaler-capex", "MARKET_STRUCTURE", "INCREASE", "MODERATE", "EXTERNAL"),
    ]


def _data_center_growth_claim() -> Claim:
    return _claim("dc-154-growth", "DEMAND_SIGNAL", "INCREASE", "EXTREME", "REALIZED")


def _cost_claim() -> Claim:
    return _claim("cost-signal", "COST_SIGNAL", "INCREASE", "STRONG", "REALIZED")


def _claim(
    claim_id: str,
    subject: ClaimSubject,
    direction: ClaimDirection,
    magnitude: MagnitudeQualifier,
    nature: ClaimNature,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="Data Center revenue and AI demand indicators changed.",
        claim_subject=subject,
        claim_nature=nature,
        direction=direction,
        magnitude_qualifier=magnitude,
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.95,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=SourceRef(
            discovery_channel="rss_aggregator",
            content_source="10-Q",
            source_reliability=0.95,
        ),
        chunk_ref=f"chunk-{claim_id}",
        published_date=date(2026, 5, 22),
    )


def _company() -> dict[str, float]:
    return {
        "operating_margin": 0.56,
        "tax_rate": 0.13,
        "wacc_estimate": 0.10,
        "competitive_advantage_score": 0.8,
        "industry_top_decile": 0.70,
        "statutory_tax_rate": 0.21,
    }


def _load_multiplicands() -> Multiplicands:
    path = Path(__file__).resolve().parents[2] / "data" / "nvda" / "multiplicands.toml"
    with path.open("rb") as file:
        data = tomllib.load(file)
    return Multiplicands(
        data_center=cast(TomlSection, data["data_center"]),
        gaming=cast(Mapping[str, TomlSpec], data["gaming"]),
        pro_viz=cast(Mapping[str, TomlSpec], data["pro_viz"]),
        auto=cast(Mapping[str, TomlSpec], data["auto"]),
    )


def main() -> None:
    result = run_nvda_spike()
    print(f"NVDA spike fair value median: {result.fair_value_median_usd / 1e12:.2f}T USD")
    print(
        f"P10/P90 band: {result.fair_value_p10_usd / 1e12:.2f}T / "
        f"{result.fair_value_p90_usd / 1e12:.2f}T USD"
    )
    print(f"reject_rate: {result.reject_rate:.2%}")


if __name__ == "__main__":
    main()
