"""Deterministic Type-1 narrative tension axis generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import numpy as np
from numpy.typing import NDArray

from dcf_engine.lifecycle import LifecycleStage

DEFAULT_EXPLAINED_VARIANCE_THRESHOLD: Final = 0.80
DEFAULT_MAX_AXES: Final = 3
ZERO_VARIANCE_TOLERANCE: Final = 1e-12

type PullVector = Sequence[float] | NDArray[np.float64]
type TamStructure = Mapping[str, object]


@dataclass(frozen=True)
class PullSignature:
    assumption_id: str
    values: PullVector
    lifecycle_stage: LifecycleStage = "growth"
    tam_structure: TamStructure = field(default_factory=dict)


@dataclass(frozen=True)
class NarrativeAxis:
    axis_index: int
    explained_variance_ratio: float
    loadings: Mapping[str, float]


def generate_narrative_axes(
    signatures: Sequence[PullSignature],
    *,
    explained_variance_threshold: float = DEFAULT_EXPLAINED_VARIANCE_THRESHOLD,
    max_axes: int = DEFAULT_MAX_AXES,
) -> tuple[NarrativeAxis, ...]:
    """Reduce finite Type-1 pull signatures into dominant assumption-space axes."""

    _validate_axis_controls(
        explained_variance_threshold=explained_variance_threshold,
        max_axes=max_axes,
    )
    ordered_signatures = _ordered_signatures(signatures)
    matrix = _signature_matrix(ordered_signatures)
    _validate_measurement_axis(ordered_signatures)

    left_singular_vectors, singular_values, _ = np.linalg.svd(matrix, full_matrices=False)
    variances = np.square(singular_values)
    positive_variance_mask = variances > ZERO_VARIANCE_TOLERANCE
    positive_variances = variances[positive_variance_mask]
    if positive_variances.size == 0:
        raise ValueError("signature matrix must contain non-zero pull variance")

    total_variance = float(np.sum(positive_variances))
    explained_variance_ratios = positive_variances / total_variance
    axis_count = min(
        _axis_count_for_threshold(explained_variance_ratios, explained_variance_threshold),
        max_axes,
    )

    assumption_ids = tuple(signature.assumption_id for signature in ordered_signatures)
    axes: list[NarrativeAxis] = []
    positive_component_indexes = np.flatnonzero(positive_variance_mask)
    for axis_index, component_index in enumerate(positive_component_indexes[:axis_count]):
        loadings = _deterministic_loadings(
            assumption_ids,
            left_singular_vectors[:, component_index],
        )
        axes.append(
            NarrativeAxis(
                axis_index=axis_index,
                explained_variance_ratio=float(explained_variance_ratios[axis_index]),
                loadings=loadings,
            )
        )
    return tuple(axes)


def _validate_axis_controls(
    *,
    explained_variance_threshold: float,
    max_axes: int,
) -> None:
    if not np.isfinite(explained_variance_threshold) or not (
        0.0 < explained_variance_threshold <= 1.0
    ):
        raise ValueError("explained_variance_threshold must be finite and in (0, 1]")
    if max_axes < 1:
        raise ValueError("max_axes must be at least 1")


def _ordered_signatures(signatures: Sequence[PullSignature]) -> tuple[PullSignature, ...]:
    if not signatures:
        raise ValueError("signatures must be non-empty")

    assumption_ids = [signature.assumption_id for signature in signatures]
    if any(not assumption_id for assumption_id in assumption_ids):
        raise ValueError("assumption_id must be non-empty")
    if len(set(assumption_ids)) != len(assumption_ids):
        raise ValueError("assumption_id values must be unique")
    return tuple(sorted(signatures, key=lambda signature: signature.assumption_id))


def _signature_matrix(signatures: Sequence[PullSignature]) -> NDArray[np.float64]:
    first_values = _signature_array(signatures[0])
    expected_shape = first_values.shape
    rows = [first_values]

    for signature in signatures[1:]:
        values = _signature_array(signature)
        if values.shape != expected_shape:
            raise ValueError("signature vectors must share the same shape")
        rows.append(values)

    return np.vstack(rows)


def _signature_array(signature: PullSignature) -> NDArray[np.float64]:
    values = np.asarray(signature.values, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("signature values must be a non-empty one-dimensional vector")
    if not np.all(np.isfinite(values)):
        raise ValueError("signature values must be finite")
    return values


def _validate_measurement_axis(signatures: Sequence[PullSignature]) -> None:
    first_signature = signatures[0]
    lifecycle_stage = first_signature.lifecycle_stage
    tam_structure = first_signature.tam_structure

    # Type-1 axes are parametric pulls within one measurement frame, not Type-2 selection.
    for signature in signatures:
        if (
            signature.lifecycle_stage != lifecycle_stage
            or signature.tam_structure != tam_structure
        ):
            raise ValueError(
                "signatures must share one measurement axis "
                "(same lifecycle_stage and tam_structure)"
            )


def _axis_count_for_threshold(
    explained_variance_ratios: NDArray[np.float64],
    explained_variance_threshold: float,
) -> int:
    cumulative_variance = np.cumsum(explained_variance_ratios)
    threshold_indexes = np.flatnonzero(cumulative_variance >= explained_variance_threshold)
    if threshold_indexes.size == 0:
        return int(explained_variance_ratios.size)
    return int(threshold_indexes[0]) + 1


def _deterministic_loadings(
    assumption_ids: Sequence[str],
    raw_loadings: NDArray[np.float64],
) -> dict[str, float]:
    loadings = np.asarray(raw_loadings, dtype=np.float64).copy()
    dominant_index = int(np.argmax(np.abs(loadings)))
    if loadings[dominant_index] < 0.0:
        loadings *= -1.0
    return {
        assumption_id: float(loading)
        for assumption_id, loading in zip(assumption_ids, loadings, strict=True)
    }
