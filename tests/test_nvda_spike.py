from pathlib import Path

import numpy as np
import pytest

from dcf_engine import nvda_spike
from dcf_engine.claim import Claim
from dcf_engine.monte_carlo import MonteCarloConfig, MonteCarloResult, mc_run
from dcf_engine.nvda_spike import run_nvda_spike
from dcf_engine.routing import route_claims_to_factors


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


def test_positive_operating_claims_do_not_reduce_nvda_below_baseline() -> None:
    baseline = _spike_fair_value_median([])
    positive_operating = _spike_fair_value_median(nvda_spike._nvda_claims())

    assert positive_operating >= baseline


def test_spike_report_exists_with_section_18_results() -> None:
    report = Path("docs/nvda-spike-report.md")

    assert report.exists()
    text = report.read_text()
    assert "reject_rate" in text
    assert "Data Center" in text


def test_nvda_spike_pairs_tam_with_accepted_mc_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tam_values = iter([100.0, 200.0, 300.0, 400.0])
    captured_tam: list[np.ndarray] = []

    def fake_sample_tam_total(rng: np.random.Generator) -> float:
        return next(tam_values)

    def fake_mc_run(*args: object, **kwargs: object) -> MonteCarloResult:
        return MonteCarloResult(
            samples={
                "REVENUE_CAGR": np.array([0.1, 0.2]),
                "MARKET_SHARE": np.array([0.5, 0.5]),
                "OPERATING_MARGIN": np.array([0.3, 0.3]),
                "TAX_RATE": np.array([0.2, 0.2]),
                "WACC": np.array([0.1, 0.1]),
                "DEFAULT_PROBABILITY": np.array([0.01, 0.01]),
            },
            reject_rate=0.5,
            accepted_indices=np.array([1, 3]),
        )

    def fake_fair_values(
        tam_samples: np.ndarray, samples: dict[str, np.ndarray]
    ) -> np.ndarray:
        captured_tam.append(tam_samples)
        return np.array([1.0, 2.0])

    monkeypatch.setattr(nvda_spike, "sample_tam_total", fake_sample_tam_total)
    monkeypatch.setattr(nvda_spike, "mc_run", fake_mc_run)
    monkeypatch.setattr(nvda_spike, "_fair_values", fake_fair_values)

    result = run_nvda_spike(seed=20260603, iterations=4)

    np.testing.assert_array_equal(captured_tam[0], np.array([200.0, 400.0]))
    assert result.reject_rate == pytest.approx(0.5)


def test_spike_valuation_approximations_are_named_and_documented() -> None:
    assert pytest.approx(0.05) == nvda_spike.NVDA_SPIKE_TERMINAL_MARGIN_FLOOR
    assert pytest.approx(1.70) == nvda_spike.NVDA_SPIKE_FCFF_TERMINAL_MULTIPLE
    assert pytest.approx(0.035) == nvda_spike.NVDA_SPIKE_TERMINAL_GROWTH
    assert pytest.approx(0.025) == nvda_spike.NVDA_SPIKE_DISCOUNT_SPREAD_FLOOR

    text = Path("docs/nvda-spike-report.md").read_text()
    assert "spike-only Gordon proxy" in text


def _spike_fair_value_median(claims: list[Claim]) -> float:
    rng = np.random.default_rng(20260607)
    tam_samples = np.array([nvda_spike.sample_tam_total(rng) for _ in range(1_000)], dtype=float)
    factors = route_claims_to_factors(claims, "growth")
    result = mc_run(
        factors,
        nvda_spike._assumptions(tam_mu=float(tam_samples.mean())),
        "growth",
        "normal",
        nvda_spike._company(),
        MonteCarloConfig(iterations=1_000, seed=20260607),
    )
    fair_values = nvda_spike._fair_values(tam_samples[result.accepted_indices], result.samples)
    return float(np.median(fair_values))
