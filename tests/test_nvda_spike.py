from pathlib import Path

import numpy as np
import pytest

from dcf_engine.nvda_spike import run_nvda_spike


def test_nvda_spike_end_to_end_dod() -> None:
    result = run_nvda_spike(seed=20260603, iterations=1_000)

    assert result.tam_mean_usd == pytest.approx(430_000_000_000, rel=0.15)
    assert 2.7e12 <= result.fair_value_median_usd <= 8.2e12
    assert result.reject_rate < 0.30
    assert result.revenue_cagr_with_demand_claim_mu > result.revenue_cagr_baseline_mu
    assert result.margin_with_cost_claim_mu < result.margin_baseline_mu
    np.testing.assert_allclose(
        result.fair_value_samples_usd,
        run_nvda_spike(seed=20260603, iterations=1_000).fair_value_samples_usd,
    )


def test_spike_report_exists_with_section_18_results() -> None:
    report = Path("docs/nvda-spike-report.md")

    assert report.exists()
    text = report.read_text()
    assert "reject_rate" in text
    assert "Data Center" in text
