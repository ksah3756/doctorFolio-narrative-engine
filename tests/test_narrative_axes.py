import numpy as np
import pytest

from dcf_engine.narrative_axes import PullSignature, generate_narrative_axes


def test_rejects_empty_signature_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        generate_narrative_axes(())


def test_rejects_signature_vectors_with_mismatched_shapes() -> None:
    signatures = (
        PullSignature(assumption_id="revenue_cagr", values=(0.20, 0.10)),
        PullSignature(assumption_id="operating_margin", values=(0.30,)),
    )

    with pytest.raises(ValueError, match="same shape"):
        generate_narrative_axes(signatures)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nan_or_infinite_signature_values(bad_value: float) -> None:
    signatures = (
        PullSignature(assumption_id="revenue_cagr", values=(0.20, bad_value)),
        PullSignature(assumption_id="operating_margin", values=(0.30, 0.40)),
    )

    with pytest.raises(ValueError, match="finite"):
        generate_narrative_axes(signatures)


def test_recovers_dominant_component_for_synthetic_one_axis_contested_set() -> None:
    signatures = (
        PullSignature(assumption_id="a_revenue_cagr", values=(3.0, 0.0)),
        PullSignature(assumption_id="b_operating_margin", values=(2.0, 0.0)),
        PullSignature(assumption_id="c_wacc", values=(-1.0, 0.0)),
    )

    axes = generate_narrative_axes(signatures)

    assert len(axes) == 1
    assert axes[0].explained_variance_ratio == pytest.approx(1.0)
    expected_scale = np.sqrt(14.0)
    assert axes[0].loadings == pytest.approx(
        {
            "a_revenue_cagr": 3.0 / expected_scale,
            "b_operating_margin": 2.0 / expected_scale,
            "c_wacc": -1.0 / expected_scale,
        }
    )


def test_respects_explained_variance_threshold_and_max_axes_cap() -> None:
    signatures = (
        PullSignature(assumption_id="a_revenue_cagr", values=(3.0, 0.0, 0.0)),
        PullSignature(assumption_id="b_operating_margin", values=(0.0, 2.0, 0.0)),
        PullSignature(assumption_id="c_wacc", values=(0.0, 0.0, 1.0)),
    )

    axes = generate_narrative_axes(
        signatures,
        explained_variance_threshold=0.95,
        max_axes=2,
    )

    assert len(axes) == 2
    assert sum(axis.explained_variance_ratio for axis in axes) == pytest.approx(13.0 / 14.0)


def test_uses_deterministic_component_orientation_for_repeated_runs() -> None:
    signatures = (
        PullSignature(assumption_id="b_operating_margin", values=(-2.0, 0.0)),
        PullSignature(assumption_id="a_revenue_cagr", values=(-3.0, 0.0)),
        PullSignature(assumption_id="c_wacc", values=(1.0, 0.0)),
    )

    first_axes = generate_narrative_axes(signatures)
    second_axes = generate_narrative_axes(signatures)

    assert first_axes == second_axes
    assert first_axes[0].loadings["a_revenue_cagr"] > 0.0


def test_preserves_stable_assumption_id_ordering_in_returned_axis_loadings() -> None:
    signatures = (
        PullSignature(assumption_id="wacc", values=(1.0, 0.0)),
        PullSignature(assumption_id="revenue_cagr", values=(2.0, 0.0)),
        PullSignature(assumption_id="operating_margin", values=(3.0, 0.0)),
    )

    axes = generate_narrative_axes(signatures)

    assert tuple(axes[0].loadings) == (
        "operating_margin",
        "revenue_cagr",
        "wacc",
    )
