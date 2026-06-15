from datetime import date

from dcf_engine.assumption import AssumptionState, ScaleSpec
from dcf_engine.claim import Claim, ClaimDirection, ClaimSubject, ExtractionQuality, SourceRef
from dcf_engine.factor import FactorState
from dcf_engine.loading import apply_factor_loadings, apply_mean_reversion
from dcf_engine.routing import route_claims_to_factors


def test_routing_keeps_fact_direction_separate_from_value_sign() -> None:
    demand = _claim("DEMAND_SIGNAL", "INCREASE")
    cost = _claim("COST_SIGNAL", "INCREASE")

    factors = route_claims_to_factors([demand, cost], "growth")

    assert factors["DemandStrength"].current_value > 0
    assert factors["OperatingEfficiency"].current_value < 0


def test_loading_shifts_revenue_up_and_margin_down_from_shared_factors() -> None:
    factors = route_claims_to_factors(
        [_claim("DEMAND_SIGNAL", "INCREASE"), _claim("COST_SIGNAL", "INCREASE")],
        "growth",
    )
    revenue = _assumption("REVENUE_CAGR", 0.22, 0.08)
    margin = _assumption("OPERATING_MARGIN", 0.56, 0.08)

    shifted = apply_factor_loadings(
        [revenue, margin], factors, stage="growth", company=_company(), t_year=1.0
    )

    assert shifted["REVENUE_CAGR"].current_mu > revenue.current_mu
    assert shifted["OPERATING_MARGIN"].current_mu < margin.current_mu


def test_sales_to_capital_reversion_floor_uses_roic_equals_wacc_relation() -> None:
    asm = _assumption("SALES_TO_CAPITAL_RATIO", 2.0, 0.3)

    reverted = apply_mean_reversion(asm, t_year=10.0, company=_company())

    assert reverted > asm.current_mu


def test_default_probability_has_narrative_cap() -> None:
    default_probability = _assumption("DEFAULT_PROBABILITY", 0.015, 0.008)
    factors = {
        "FinancialStrength": FactorState(name="FinancialStrength", current_value=-3.0),
        "OperatingEfficiency": FactorState(name="OperatingEfficiency", current_value=-3.0),
        "MacroCondition": FactorState(name="MacroCondition", current_value=-3.0),
    }

    shifted = apply_factor_loadings(
        [default_probability], factors, stage="growth", company=_company(), t_year=1.0
    )

    assert shifted["DEFAULT_PROBABILITY"].current_mu <= 0.05


def test_wacc_has_narrow_narrative_premium_cap() -> None:
    wacc = _assumption("WACC", 0.095, 0.012)
    factors = {
        "FinancialStrength": FactorState(name="FinancialStrength", current_value=-3.0),
        "OperatingEfficiency": FactorState(name="OperatingEfficiency", current_value=-3.0),
        "MacroCondition": FactorState(name="MacroCondition", current_value=-3.0),
    }

    shifted = apply_factor_loadings([wacc], factors, stage="growth", company=_company(), t_year=1.0)

    assert shifted["WACC"].current_mu <= wacc.base_mu + 0.015


def _claim(subject: ClaimSubject, direction: ClaimDirection) -> Claim:
    return Claim(
        claim_id=f"{subject}-{direction}",
        claim_text="NVDA narrative claim.",
        claim_subject=subject,
        claim_nature="REALIZED",
        direction=direction,
        magnitude_qualifier="STRONG",
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
        chunk_ref="chunk",
        published_date=date(2026, 5, 22),
    )


def _assumption(name: str, mu: float, sigma: float) -> AssumptionState:
    return AssumptionState(
        name=name,
        distribution_family="normal",
        current_mu=mu,
        current_sigma=sigma,
        base_mu=mu,
        base_sigma=sigma,
        shift_scale=ScaleSpec(center=0.05, uncertainty=0.0),
        constraints={},
        active=True,
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
