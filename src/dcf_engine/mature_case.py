"""Deterministic mature-stage historical-base proof slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean, pstdev
from typing import Final, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dcf_engine.assumption import AssumptionState, ScaleSpec, compute_reinvestment
from dcf_engine.claim import Claim, ExtractionQuality, SourceRef
from dcf_engine.distributions import DistributionFamily
from dcf_engine.lifecycle import (
    CompanySnapshot,
    LifecycleStage,
    ValuationMode,
    classify_lifecycle,
    valuation_mode_for_stage,
)
from dcf_engine.loading import apply_factor_loadings
from dcf_engine.monte_carlo import MonteCarloConfig, mc_run
from dcf_engine.routing import narrative_sensitivity, route_claims_to_factors

HISTORY_YEARS: Final = 5
MIN_SIGMA: Final = 1e-6
MATURE_CASE_T_YEAR: Final = 1.0


class AnnualObservation(BaseModel):
    """One validated annual observation used by the proof slice."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    revenue_growth: float = Field(gt=-1.0, lt=1.0)
    operating_margin: float = Field(gt=-1.0, lt=1.0)
    roic: float = Field(gt=0.0, lt=1.0)
    wacc: float = Field(gt=0.0, lt=1.0)
    tax_rate: float = Field(ge=0.0, lt=1.0)
    nopat: float = Field(gt=0.0)


class MatureHistory(BaseModel):
    """Exactly five annual observations for a mature fixture company."""

    model_config = ConfigDict(frozen=True)

    observations: tuple[AnnualObservation, ...]

    @model_validator(mode="after")
    def require_five_observations(self) -> Self:
        if len(self.observations) != HISTORY_YEARS:
            raise ValueError("history must contain exactly five annual observations")
        return self


@dataclass(frozen=True)
class MatureCaseResult:
    stage: LifecycleStage
    valuation_mode: ValuationMode
    operating_margin_base_mu: float
    roic_base_mu: float
    wacc_base_mu: float
    active_assumptions: tuple[str, ...]
    inactive_assumptions: tuple[str, ...]
    narrative_sensitivity: float
    operating_efficiency_factor: float
    operating_margin_baseline_mu: float
    operating_margin_claim_mu: float
    wacc_baseline_mu: float
    wacc_claim_mu: float
    revenue_growth_samples: np.ndarray
    operating_margin_samples: np.ndarray
    roic_samples: np.ndarray
    wacc_samples: np.ndarray
    reinvestment_samples: np.ndarray
    reinvestment_p10: float
    reinvestment_median: float
    reinvestment_p90: float
    reject_rate: float


def run_mature_case(
    history: MatureHistory, *, seed: int = 20260624, iterations: int = 1_000
) -> MatureCaseResult:
    """Run the mature lifecycle, narrative, sampling, and reinvestment path."""
    observations = history.observations
    assumptions = _assumptions(observations)
    company, stage = _company_context(observations)
    factors = route_claims_to_factors([_cost_pressure_claim()], stage)
    baseline = apply_factor_loadings(
        assumptions, {}, stage=stage, company=company, t_year=MATURE_CASE_T_YEAR
    )
    shifted = apply_factor_loadings(
        assumptions, factors, stage=stage, company=company, t_year=MATURE_CASE_T_YEAR
    )
    mc_result = mc_run(
        factors,
        assumptions,
        stage,
        "normal",
        company,
        MonteCarloConfig(iterations=iterations, seed=seed, t_year=MATURE_CASE_T_YEAR),
    )
    growth = _readonly(mc_result.samples["REVENUE_CAGR"])
    margin = _readonly(mc_result.samples["OPERATING_MARGIN"])
    roic = _readonly(mc_result.samples["ROIC"])
    wacc = _readonly(mc_result.samples["WACC"])
    # mature 재투자는 기존 lifecycle 분기인 NOPAT * growth / ROIC를 그대로 사용한다.
    reinvestment = _readonly(
        np.fromiter(
            (
                compute_reinvestment(
                    stage,
                    delta_revenue=0.0,
                    nopat=observations[-1].nopat,
                    growth=growth_value,
                    tool_value=roic_value,
                )
                for growth_value, roic_value in zip(growth, roic, strict=True)
            ),
            dtype=float,
            count=growth.size,
        )
    )
    if not all(np.isfinite(values).all() for values in (growth, margin, roic, wacc, reinvestment)):
        raise RuntimeError("mature case produced non-finite samples")
    p10, median, p90 = np.percentile(reinvestment, (10, 50, 90))
    active = tuple(assumption.name for assumption in assumptions if assumption.active)
    inactive = tuple(assumption.name for assumption in assumptions if not assumption.active)
    return MatureCaseResult(
        stage=stage,
        valuation_mode=valuation_mode_for_stage(stage),
        operating_margin_base_mu=baseline["OPERATING_MARGIN"].base_mu,
        roic_base_mu=baseline["ROIC"].base_mu,
        wacc_base_mu=baseline["WACC"].base_mu,
        active_assumptions=active,
        inactive_assumptions=inactive,
        narrative_sensitivity=narrative_sensitivity(stage, "COST_SIGNAL"),
        operating_efficiency_factor=factors["OperatingEfficiency"].current_value,
        operating_margin_baseline_mu=baseline["OPERATING_MARGIN"].current_mu,
        operating_margin_claim_mu=shifted["OPERATING_MARGIN"].current_mu,
        wacc_baseline_mu=baseline["WACC"].current_mu,
        wacc_claim_mu=shifted["WACC"].current_mu,
        revenue_growth_samples=growth,
        operating_margin_samples=margin,
        roic_samples=roic,
        wacc_samples=wacc,
        reinvestment_samples=reinvestment,
        reinvestment_p10=float(p10),
        reinvestment_median=float(median),
        reinvestment_p90=float(p90),
        reject_rate=mc_result.reject_rate,
    )


def _assumptions(observations: tuple[AnnualObservation, ...]) -> list[AssumptionState]:
    specs: tuple[tuple[str, DistributionFamily, tuple[float, ...], bool], ...] = (
        ("REVENUE_CAGR", "normal", tuple(item.revenue_growth for item in observations), True),
        ("OPERATING_MARGIN", "normal", tuple(item.operating_margin for item in observations), True),
        ("TAX_RATE", "normal", tuple(item.tax_rate for item in observations), True),
        ("ROIC", "lognormal", tuple(item.roic for item in observations), True),
        ("WACC", "lognormal", tuple(item.wacc for item in observations), True),
        ("SALES_TO_CAPITAL_RATIO", "lognormal", (1.0,) * HISTORY_YEARS, False),
    )
    return [_assumption(name, family, values, active) for name, family, values, active in specs]


def _assumption(
    name: str, family: DistributionFamily, values: tuple[float, ...], active: bool
) -> AssumptionState:
    base_mu = fmean(values)
    sigma = max(pstdev(values), MIN_SIGMA)
    return AssumptionState(
        name=name,
        distribution_family=family,
        current_mu=base_mu,
        current_sigma=sigma,
        base_mu=base_mu,
        base_sigma=sigma,
        shift_scale=ScaleSpec(center=sigma, uncertainty=0.0),
        constraints={"low": 0.0, "high": 1.0},
        active=active,
    )


def _company_context(
    observations: tuple[AnnualObservation, ...],
) -> tuple[dict[str, float], LifecycleStage]:
    margin = fmean(item.operating_margin for item in observations)
    tax_rate = fmean(item.tax_rate for item in observations)
    wacc = fmean(item.wacc for item in observations)
    snapshot = CompanySnapshot(
        revenue_cagr_3y=fmean(item.revenue_growth for item in observations[-3:]),
        operating_margin=margin,
        fcfe_recent=observations[-1].nopat,
        reinvestment_rate=0.20,
        years_since_ipo=25,
        margin_trend="stable",
        returns_capital=True,
    )
    stage = classify_lifecycle(snapshot)
    if stage != "mature" or valuation_mode_for_stage(stage) != "established":
        raise ValueError("historical fixture must classify as mature/established")
    return {
        "operating_margin": margin,
        "tax_rate": tax_rate,
        "wacc_estimate": wacc,
        "competitive_advantage_score": 0.7,
        "industry_top_decile": 0.35,
        "statutory_tax_rate": 0.25,
    }, stage


def _cost_pressure_claim() -> Claim:
    return Claim(
        claim_id="mature-cost-pressure",
        claim_text="Operating expenses increased as labor and input costs rose.",
        claim_subject="COST_SIGNAL",
        claim_nature="REALIZED",
        direction="INCREASE",
        magnitude_qualifier="STRONG",
        extraction_quality=ExtractionQuality(
            verbatim_overlap=0.95,
            numeric_consistency=True,
            temporal_consistency=True,
            entity_consistency=True,
        ),
        source_ref=SourceRef(
            discovery_channel="direct",
            content_source="10-K",
            source_reliability=0.95,
        ),
        chunk_ref="mature-case-history",
        published_date=date(2026, 6, 24),
    )


def _readonly(values: np.ndarray) -> np.ndarray:
    copied = values.copy()
    copied.setflags(write=False)
    return copied
