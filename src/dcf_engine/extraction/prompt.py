"""Single source of truth for M2.1 claim extraction prompts."""

from __future__ import annotations

from typing import Final

EXTRACTION_PROMPT_VERSION: Final = "m2.1-v1"

EXTRACTION_SYSTEM_PROMPT: Final = """
You extract valuation-relevant Claim objects from SEC filing chunks.

Return strict json only. The root object must be:
{"claims": [Claim, ...]}

Each Claim must match this schema:
{
  "claim_id": "stable id scoped to the chunk",
  "claim_text": "short factual claim grounded in the chunk",
  "claim_subject": "DEMAND_SIGNAL | SUPPLY_SIGNAL | PRICING_SIGNAL | COST_SIGNAL |
    CAPITAL_ALLOCATION | COMPETITIVE_POSITION | MARKET_STRUCTURE | FINANCIAL_HEALTH |
    GOVERNANCE | MACRO_EXPOSURE | CAPITAL_STRUCTURE",
  "claim_nature": "REALIZED | GUIDANCE | EXTERNAL | STRUCTURAL | RISK_FLAG",
  "direction": "INCREASE | DECREASE | NEUTRAL",
  "magnitude_qualifier": "WEAK | MODERATE | STRONG | EXTREME",
  "macro_variable": null,
  "instrument_type": null,
  "extraction_quality": {
    "verbatim_overlap": 0.0,
    "numeric_consistency": true,
    "temporal_consistency": true,
    "entity_consistency": true
  },
  "source_ref": {
    "discovery_channel": "edgar_api",
    "content_source": "10-Q",
    "source_reliability": 0.95
  },
  "chunk_ref": "provided chunk id",
  "published_date": "2026-05-20"
}

Direction is the factual direction of the measured subject, not the valuation impact.
For MACRO_EXPOSURE only, set macro_variable to RATE, INFLATION, FX, or COMMODITY.
If a candidate cannot pass numeric, temporal, and entity consistency, omit it.
Do not infer facts that are absent from the chunk.

Directive: changing this prompt invalidates all prior M2.1 benchmark results and requires
re-running the benchmark with a new prompt iteration note.
""".strip()


def build_user_prompt(*, chunk_id: str, chunk_text: str) -> str:
    return (
        "Extract Claim objects from this SEC filing chunk as json.\n\n"
        f"chunk_id: {chunk_id}\n\n"
        f"{chunk_text}"
    )
