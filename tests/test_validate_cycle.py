from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from dcf_engine.claim import Claim, ExtractionQuality, SourceRef
from dcf_engine.validate_cycle import ValidationReport, run_validation_cycle


VALIDATION_TEST_SEED = 20260629
VALIDATION_TEST_ITERATIONS = 300


def test_validation_cycle_returns_ordered_fair_value_distribution() -> None:
    claims = [_claim("baseline-neutral", "DEMAND_SIGNAL", "NEUTRAL")]

    report = run_validation_cycle(
        claims,
        stage="growth",
        seed=VALIDATION_TEST_SEED,
        iterations=VALIDATION_TEST_ITERATIONS,
    )

    assert isinstance(report, ValidationReport)
    assert report.claims_used == len(claims)
    assert report.factors_touched == ("DemandStrength", "CompetitiveAdvantage")
    assert report.fair_value_samples_usd.dtype == np.float64
    assert len(report.fair_value_samples_usd) == VALIDATION_TEST_ITERATIONS
    assert report.fair_value_p10_usd <= report.fair_value_median_usd <= report.fair_value_p90_usd


def test_validation_cycle_is_deterministic_by_seed() -> None:
    claims = [_claim("demand-growth", "DEMAND_SIGNAL", "INCREASE")]

    first = run_validation_cycle(
        claims,
        stage="growth",
        seed=VALIDATION_TEST_SEED,
        iterations=VALIDATION_TEST_ITERATIONS,
    )
    repeat = run_validation_cycle(
        claims,
        stage="growth",
        seed=VALIDATION_TEST_SEED,
        iterations=VALIDATION_TEST_ITERATIONS,
    )
    different_seed = run_validation_cycle(
        claims,
        stage="growth",
        seed=VALIDATION_TEST_SEED + 1,
        iterations=VALIDATION_TEST_ITERATIONS,
    )

    np.testing.assert_array_equal(first.fair_value_samples_usd, repeat.fair_value_samples_usd)
    assert first.fair_value_median_usd == repeat.fair_value_median_usd
    assert not np.array_equal(first.fair_value_samples_usd, different_seed.fair_value_samples_usd)


def test_validation_cycle_rejects_empty_claims() -> None:
    with pytest.raises(ValueError, match="claims must not be empty"):
        run_validation_cycle([], stage="growth")


def test_positive_claim_moves_fair_value_distribution_up_from_neutral_baseline() -> None:
    baseline_claim = _claim("baseline-neutral", "DEMAND_SIGNAL", "NEUTRAL")
    positive_claim = _claim("positive-demand", "DEMAND_SIGNAL", "INCREASE")

    baseline = run_validation_cycle(
        [baseline_claim],
        stage="growth",
        seed=VALIDATION_TEST_SEED,
        iterations=VALIDATION_TEST_ITERATIONS,
    )
    positive = run_validation_cycle(
        [baseline_claim, positive_claim],
        stage="growth",
        seed=VALIDATION_TEST_SEED,
        iterations=VALIDATION_TEST_ITERATIONS,
    )

    assert positive.fair_value_median_usd > baseline.fair_value_median_usd


def test_validation_cycle_does_not_mutate_input_claims() -> None:
    claims = [_claim("demand-growth", "DEMAND_SIGNAL", "INCREASE")]
    original = tuple(claims)

    run_validation_cycle(
        claims,
        stage="growth",
        seed=VALIDATION_TEST_SEED,
        iterations=VALIDATION_TEST_ITERATIONS,
    )

    assert tuple(claims) == original


def _claim(
    claim_id: str,
    subject: str,
    direction: str,
    *,
    text: str = "Data Center revenue increased as AI demand expanded.",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=text,
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
        chunk_ref=f"chunk-{claim_id}",
        published_date=date(2026, 5, 22),
    )
