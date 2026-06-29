from __future__ import annotations

import pytest

from dcf_engine.claim import Claim, ClaimDirection, ClaimSubject
from dcf_engine.extraction.calibration import calibrate_extraction_replay
from dcf_engine.extraction.client import ExtractionResponse, TokenUsage


def test_repeated_matching_subject_direction_pass_default_threshold() -> None:
    responses = [
        _response(
            "chunk-1",
            _claim(
                claim_id=f"claim-{index}",
                text="Data center revenue increased 50%.",
                subject="DEMAND_SIGNAL",
                direction="INCREASE",
            ),
        )
        for index in range(10)
    ]

    result = calibrate_extraction_replay(responses)

    assert result.passed is True
    assert result.valid_repeat_count == 10
    assert result.invalid_repeat_count == 0
    assert result.agreement_rate == 1.0
    assert result.threshold == 0.9
    assert result.unstable_groups == ()


def test_direction_drift_below_threshold_fails_with_unstable_group() -> None:
    stable_repeats = [
        _response(
            "chunk-1",
            _claim(
                claim_id=f"increase-{index}",
                text="Gross margin improved year over year.",
                subject="COST_SIGNAL",
                direction="INCREASE",
            ),
        )
        for index in range(8)
    ]
    drift_repeats = [
        _response(
            "chunk-1",
            _claim(
                claim_id=f"decrease-{index}",
                text="Gross margin improved year over year.",
                subject="COST_SIGNAL",
                direction="DECREASE",
            ),
        )
        for index in range(2)
    ]

    result = calibrate_extraction_replay([*stable_repeats, *drift_repeats])

    assert result.passed is False
    assert result.agreement_rate == 0.8
    assert len(result.unstable_groups) == 1
    unstable = result.unstable_groups[0]
    assert unstable.chunk_id == "chunk-1"
    assert unstable.claim_group == "gross margin improved year over year"
    assert unstable.valid_repeat_count == 10
    assert unstable.agreement_rate == 0.8
    assert unstable.label_counts == {
        ("COST_SIGNAL", "INCREASE"): 8,
        ("COST_SIGNAL", "DECREASE"): 2,
    }


def test_schema_invalid_responses_are_counted_but_excluded_from_agreement() -> None:
    responses = [
        _response(
            "chunk-1",
            _claim(
                claim_id=f"valid-{index}",
                text="Operating expenses rose with investment.",
                subject="COST_SIGNAL",
                direction="INCREASE",
            ),
        )
        for index in range(9)
    ]
    responses.append(
        ExtractionResponse(
            chunk_id="chunk-1",
            claims=[],
            usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
            latency_ms=0,
            schema_valid=False,
            error="ValidationError",
        )
    )

    result = calibrate_extraction_replay(responses)

    assert result.passed is True
    assert result.valid_repeat_count == 9
    assert result.invalid_repeat_count == 1
    assert result.agreement_rate == 1.0


def test_empty_calibration_input_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="at least two replay responses"):
        calibrate_extraction_replay([])


def test_single_repeat_input_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="at least two replay responses"):
        calibrate_extraction_replay(
            [
                _response(
                    "chunk-1",
                    _claim(
                        claim_id="claim-1",
                        text="Revenue increased.",
                        subject="DEMAND_SIGNAL",
                        direction="INCREASE",
                    ),
                )
            ]
        )


def test_calibration_result_order_is_deterministic_and_auditable() -> None:
    responses = [
        _response(
            "chunk-b",
            _claim(
                claim_id="b-1",
                text="Pricing weakened in gaming.",
                subject="PRICING_SIGNAL",
                direction="DECREASE",
            ),
        ),
        _response(
            "chunk-a",
            _claim(
                claim_id="a-1",
                text="Supply improved for accelerators.",
                subject="SUPPLY_SIGNAL",
                direction="INCREASE",
            ),
        ),
        _response(
            "chunk-b",
            _claim(
                claim_id="b-2",
                text="Pricing weakened in gaming.",
                subject="PRICING_SIGNAL",
                direction="INCREASE",
            ),
        ),
        _response(
            "chunk-a",
            _claim(
                claim_id="a-2",
                text="Supply improved for accelerators.",
                subject="DEMAND_SIGNAL",
                direction="INCREASE",
            ),
        ),
    ]

    result = calibrate_extraction_replay(responses)
    repeated = calibrate_extraction_replay(list(reversed(responses)))

    assert result == repeated
    assert result.valid_repeat_count == 4
    assert result.invalid_repeat_count == 0
    assert result.agreement_rate == 0.5
    assert result.threshold == 0.9
    assert [group.chunk_id for group in result.unstable_groups] == ["chunk-a", "chunk-b"]
    assert [group.claim_group for group in result.unstable_groups] == [
        "supply improved for accelerators",
        "pricing weakened in gaming",
    ]


def _response(chunk_id: str, claim: Claim) -> ExtractionResponse:
    return ExtractionResponse(
        chunk_id=chunk_id,
        claims=[claim],
        usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
        latency_ms=0,
    )


def _claim(
    *,
    claim_id: str,
    text: str,
    subject: ClaimSubject,
    direction: ClaimDirection,
) -> Claim:
    return Claim.model_validate(
        {
            "claim_id": claim_id,
            "claim_text": text,
            "claim_subject": subject,
            "claim_nature": "REALIZED",
            "direction": direction,
            "magnitude_qualifier": "STRONG",
            "macro_variable": None,
            "instrument_type": None,
            "extraction_quality": {
                "verbatim_overlap": 1.0,
                "numeric_consistency": True,
                "temporal_consistency": True,
                "entity_consistency": True,
            },
            "source_ref": {
                "discovery_channel": "edgar_api",
                "content_source": "10-Q",
                "source_reliability": 0.95,
            },
            "chunk_ref": "chunk-1",
            "published_date": "2026-05-20",
        }
    )
