from dcf_engine.assumption import REINVESTMENT_TOOL_BY_STAGE, compute_reinvestment
from dcf_engine.lifecycle import CompanySnapshot, classify_lifecycle, valuation_mode_for_stage


def test_classifies_nvda_as_growth_and_uses_sales_to_capital() -> None:
    company = CompanySnapshot(
        revenue_cagr_3y=0.38,
        operating_margin=0.55,
        fcfe_recent=25_000_000_000,
        reinvestment_rate=0.48,
        years_since_ipo=27,
        margin_trend="expanding",
        returns_capital=True,
    )

    stage = classify_lifecycle(company)

    assert stage == "growth"
    assert valuation_mode_for_stage(stage) == "hybrid"
    assert REINVESTMENT_TOOL_BY_STAGE[stage] == "sales_to_capital"
    assert (
        compute_reinvestment(
            stage, delta_revenue=10.0, nopat=20.0, growth=0.25, tool_value=2.5
        )
        == 4.0
    )


def test_mature_stage_uses_roic_reinvestment() -> None:
    company = CompanySnapshot(
        revenue_cagr_3y=0.04,
        operating_margin=0.22,
        fcfe_recent=1.0,
        reinvestment_rate=0.18,
        years_since_ipo=20,
        margin_trend="stable",
        returns_capital=True,
    )

    stage = classify_lifecycle(company)

    assert stage == "mature"
    assert (
        compute_reinvestment(
            stage, delta_revenue=1.0, nopat=100.0, growth=0.05, tool_value=0.20
        )
        == 25.0
    )
