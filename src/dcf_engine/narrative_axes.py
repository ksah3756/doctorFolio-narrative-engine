"""Deterministic Type-1 narrative tension axis generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final

import numpy as np
from numpy.typing import NDArray

from dcf_engine.assumption import AssumptionState
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.narrative import Narrative, NarrativeContainer

DEFAULT_EXPLAINED_VARIANCE_THRESHOLD: Final = 0.80
DEFAULT_MAX_AXES: Final = 3
DEFAULT_TYPE1_SHIFT_STRENGTH: Final = 1.0
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
class EvidencePull:
    claim_id: str
    values: PullVector


@dataclass(frozen=True)
class ContestedAssumptionPullInput:
    assumption_id: str
    supporting: Sequence[EvidencePull]
    contradicting: Sequence[EvidencePull]
    lifecycle_stage: LifecycleStage = "growth"
    tam_structure: TamStructure = field(default_factory=dict)


@dataclass(frozen=True)
class NarrativeAxis:
    axis_index: int
    explained_variance_ratio: float
    loadings: Mapping[str, float]


def build_pull_signature(contested: ContestedAssumptionPullInput) -> PullSignature:
    """Build one deterministic PullSignature from one contested assumption."""

    if not contested.assumption_id.strip():
        raise ValueError("assumption_id must be non-empty")

    signed_evidence = _signed_evidence_items(contested)
    _validate_evidence_claim_ids(tuple(evidence for _, evidence in signed_evidence))

    first_direction, first_evidence = signed_evidence[0]
    aggregate = first_direction * _evidence_pull_array(first_evidence)
    expected_shape = aggregate.shape

    for direction, evidence in signed_evidence[1:]:
        values = _evidence_pull_array(evidence)
        if values.shape != expected_shape:
            raise ValueError("evidence vectors must share the same shape")
        aggregate += direction * values

    return PullSignature(
        assumption_id=contested.assumption_id,
        values=tuple(float(value) for value in aggregate),
        lifecycle_stage=contested.lifecycle_stage,
        tam_structure=dict(contested.tam_structure),
    )


def generate_type1_narrative_candidates(
    axis: NarrativeAxis,
    *,
    assumptions: Sequence[AssumptionState],
    lifecycle_stage: LifecycleStage = "growth",
    tam_structure: TamStructure | None = None,
    shift_strength: float = DEFAULT_TYPE1_SHIFT_STRENGTH,
) -> tuple[NarrativeContainer, NarrativeContainer]:
    """Create deterministic positive/negative Type-1 candidates from one PCA axis."""

    _validate_type1_candidate_inputs(
        axis=axis,
        assumptions=assumptions,
        shift_strength=shift_strength,
    )
    measurement_tam_structure = {} if tam_structure is None else dict(tam_structure)
    return (
        _type1_candidate_container(
            axis=axis,
            assumptions=assumptions,
            polarity="positive",
            direction=1.0,
            lifecycle_stage=lifecycle_stage,
            tam_structure=measurement_tam_structure,
            shift_strength=shift_strength,
        ),
        _type1_candidate_container(
            axis=axis,
            assumptions=assumptions,
            polarity="negative",
            direction=-1.0,
            lifecycle_stage=lifecycle_stage,
            tam_structure=measurement_tam_structure,
            shift_strength=shift_strength,
        ),
    )


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


def _signed_evidence_items(
    contested: ContestedAssumptionPullInput,
) -> tuple[tuple[float, EvidencePull], ...]:
    signed_evidence = tuple(
        (1.0, evidence) for evidence in contested.supporting
    ) + tuple((-1.0, evidence) for evidence in contested.contradicting)
    if not signed_evidence:
        raise ValueError("contested evidence must be non-empty")
    return signed_evidence


def _validate_evidence_claim_ids(evidence_items: Sequence[EvidencePull]) -> None:
    claim_ids = [evidence.claim_id for evidence in evidence_items]
    if any(not claim_id.strip() for claim_id in claim_ids):
        raise ValueError("evidence claim_id must be non-empty")
    duplicate_claim_ids = {
        claim_id for claim_id in claim_ids if claim_ids.count(claim_id) > 1
    }
    if duplicate_claim_ids:
        ordered_duplicates = ", ".join(sorted(duplicate_claim_ids))
        raise ValueError(f"duplicate claim ids in contested evidence: {ordered_duplicates}")


def _evidence_pull_array(evidence: EvidencePull) -> NDArray[np.float64]:
    try:
        values = np.asarray(evidence.values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("evidence values must be numeric") from error
    if values.ndim != 1 or values.size == 0:
        raise ValueError("evidence values must be a non-empty one-dimensional vector")
    if not np.all(np.isfinite(values)):
        raise ValueError("evidence values must be finite")
    return values


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


def _validate_type1_candidate_inputs(
    *,
    axis: NarrativeAxis,
    assumptions: Sequence[AssumptionState],
    shift_strength: float,
) -> None:
    if not axis.loadings:
        raise ValueError("axis loadings must be non-empty")
    if not all(np.isfinite(loading) for loading in axis.loadings.values()):
        raise ValueError("axis loadings must be finite")
    if not np.isfinite(shift_strength) or shift_strength <= 0.0:
        raise ValueError("shift_strength must be finite and positive")
    if not assumptions:
        raise ValueError("assumptions must be non-empty")

    assumption_names = tuple(assumption.name for assumption in assumptions)
    if len(set(assumption_names)) != len(assumption_names):
        raise ValueError("assumptions must have unique names")
    missing_assumptions = set(axis.loadings) - set(assumption_names)
    if missing_assumptions:
        raise ValueError("axis loadings must reference supplied assumptions")


def _type1_candidate_container(
    *,
    axis: NarrativeAxis,
    assumptions: Sequence[AssumptionState],
    polarity: str,
    direction: float,
    lifecycle_stage: LifecycleStage,
    tam_structure: TamStructure,
    shift_strength: float,
) -> NarrativeContainer:
    narrative = Narrative.default(
        narrative_id=f"type1-axis-{axis.axis_index}-{polarity}",
        lifecycle_stage=lifecycle_stage,
        tam_structure=tam_structure,
    )
    return NarrativeContainer.single(
        narrative=narrative,
        assumptions=tuple(
            _shift_assumption_for_axis(
                assumption=assumption,
                loading=axis.loadings.get(assumption.name, 0.0),
                direction=direction,
                shift_strength=shift_strength,
            )
            for assumption in assumptions
        ),
    )


def _shift_assumption_for_axis(
    *,
    assumption: AssumptionState,
    loading: float,
    direction: float,
    shift_strength: float,
) -> AssumptionState:
    shifted_mu = (
        assumption.base_mu
        + direction * loading * shift_strength * assumption.shift_scale.center
    )
    if not np.isfinite(shifted_mu):
        raise ValueError("generated candidate assumption shifts must be finite")
    candidate_mu = float(shifted_mu)
    # Type-1 후보는 factor 입력이 아니라 valuation base 자체를 바꾸는 결정적 대안이다.
    return replace(assumption, current_mu=candidate_mu, base_mu=candidate_mu)


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
