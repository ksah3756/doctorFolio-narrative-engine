import pytest
from pydantic import ValidationError

from dcf_engine.narrative_type2 import (
    Type2NarrativeCandidate,
    build_type2_candidate_prompt,
    validate_type2_candidate_claim_ids,
)


VALID_CLAIM_IDS = {"claim-1", "claim-2", "claim-3"}


def _candidate_payload() -> dict[str, object]:
    return {
        "candidate_id": "platform",
        "thesis": "AI accelerators become a platform control point.",
        "short_description": "Platform economics rather than component supply.",
        "lifecycle_stage": "growth",
        "tam_structure": {"market": "accelerated-compute", "scope": "platform"},
        "supporting_claim_ids": ["claim-1", "claim-2"],
        "contradicting_claim_ids": ["claim-3"],
    }


@pytest.mark.parametrize(
    "missing_field",
    [
        "candidate_id",
        "thesis",
        "lifecycle_stage",
        "tam_structure",
        "supporting_claim_ids",
        "contradicting_claim_ids",
    ],
)
def test_type2_candidate_rejects_missing_structural_fields(missing_field: str) -> None:
    payload = _candidate_payload()
    del payload[missing_field]

    with pytest.raises(ValidationError):
        Type2NarrativeCandidate.model_validate(payload)


@pytest.mark.parametrize(
    "payload_update",
    [
        {"candidate_id": ""},
        {"thesis": "  "},
        {"short_description": ""},
        {"lifecycle_stage": "hypergrowth"},
        {"tam_structure": {}},
        {"supporting_claim_ids": [], "contradicting_claim_ids": []},
    ],
)
def test_type2_candidate_rejects_invalid_or_structurally_empty_values(
    payload_update: dict[str, object],
) -> None:
    payload = _candidate_payload() | payload_update

    with pytest.raises(ValidationError):
        Type2NarrativeCandidate.model_validate(payload)


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "probability",
        "weight",
        "expected_value",
        "weighted_value",
        "blended_valuation",
    ],
)
def test_type2_candidate_rejects_probability_or_blending_fields(
    forbidden_field: str,
) -> None:
    payload = _candidate_payload() | {forbidden_field: 0.5}

    with pytest.raises(ValidationError):
        Type2NarrativeCandidate.model_validate(payload)


def test_type2_candidate_validates_evidence_claim_ids_against_shared_pool() -> None:
    candidate = Type2NarrativeCandidate.model_validate(
        _candidate_payload()
        | {
            "supporting_claim_ids": ["claim-1", "missing-support"],
            "contradicting_claim_ids": ["claim-3", "missing-contradiction"],
        }
    )

    with pytest.raises(ValueError, match="unknown claim ids"):
        validate_type2_candidate_claim_ids(candidate, VALID_CLAIM_IDS)


def test_type2_candidate_prompt_has_stable_ordering_and_boundary_instructions() -> None:
    prompt = build_type2_candidate_prompt(
        company_name="NVIDIA",
        claim_text_by_id={
            "claim-2": "Data center demand expanded.",
            "claim-1": "Export controls remain a risk.",
        },
        max_candidates=3,
    )

    claim_1_index = prompt.index("- claim-1: Export controls remain a risk.")
    claim_2_index = prompt.index("- claim-2: Data center demand expanded.")
    assert claim_1_index < claim_2_index
    assert "Do not assign probabilities, weights, expected values, or blended valuations." in prompt
    assert "Do not perform valuation calculations." in prompt
    assert "Human selection happens later; propose candidates only." in prompt
    assert "Return JSON only" in prompt
