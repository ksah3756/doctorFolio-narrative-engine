from dcf_engine.extraction.prompt import EXTRACTION_SYSTEM_PROMPT


def test_prompt_documents_temporary_modality_boundary() -> None:
    prompt = EXTRACTION_SYSTEM_PROMPT

    assert "claim_modalities" in prompt
    assert "temporary extraction-time metadata" in prompt
    assert "not a final Claim field" in prompt
    assert "FACT | INTERPRETATION | PROJECTION" in prompt
