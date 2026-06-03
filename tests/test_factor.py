from datetime import UTC, datetime, timedelta

import pytest

from dcf_engine.claim import Claim, ExtractionQuality, SourceRef
from dcf_engine.factor import decay_weight, factor_uncertainty


def test_decay_weight_uses_claim_half_life() -> None:
    claim = _claim("REALIZED")
    now = datetime(2026, 6, 1, tzinfo=UTC)

    assert decay_weight(claim, now) == pytest.approx(1.0)
    older = claim.model_copy(update={"published_date": now.date() - timedelta(days=90)})
    assert decay_weight(older, now) == pytest.approx(0.5)


def test_factor_uncertainty_uses_decay_weighted_effective_n_and_stress_extra() -> None:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    recent = _claim("REALIZED")
    old = recent.model_copy(update={"published_date": now.date() - timedelta(days=90)})

    normal = factor_uncertainty("MacroCondition", "growth", "normal", [recent, old], now)
    stress = factor_uncertainty("MacroCondition", "growth", "stress", [recent, old], now)

    assert normal == pytest.approx(0.5 / (1 + 1.5) ** 0.5)
    assert stress > normal * 2


def _claim(nature: str) -> Claim:
    return Claim(
        claim_id=f"claim-{nature}",
        claim_text="Data center revenue increased.",
        claim_subject="DEMAND_SIGNAL",
        claim_nature=nature,
        direction="INCREASE",
        magnitude_qualifier="MODERATE",
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
        published_date=datetime(2026, 6, 1, tzinfo=UTC).date(),
    )
