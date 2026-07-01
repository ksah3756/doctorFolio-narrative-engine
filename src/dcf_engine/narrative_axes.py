"""Deterministic Type-1 narrative tension axis generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from dcf_engine.assumption import AssumptionState
from dcf_engine.claim import Claim
from dcf_engine.lifecycle import LifecycleStage
from dcf_engine.loading import LOADING
from dcf_engine.narrative import Narrative, NarrativeContainer
from dcf_engine.routing import _routing_for_driver, claims_to_economic_drivers, factor_shift

DEFAULT_EXPLAINED_VARIANCE_THRESHOLD: Final = 0.80
DEFAULT_MAX_AXES: Final = 3
DEFAULT_TYPE1_SHIFT_STRENGTH: Final = 1.0
DEFAULT_CONTESTED_MASS_THRESHOLD: Final = 1.0
DEFAULT_AXIS_STABILITY_THRESHOLD: Final = 0.70
CONDITIONAL_PULL_DISCOUNT: Final = 0.5
ZERO_VARIANCE_TOLERANCE: Final = 1e-12

type PullVector = Sequence[float] | NDArray[np.float64]
type TamStructure = Mapping[str, object]
type Type1AxisRejectionReason = Literal[
    "stability_below_threshold",
    "stage_c_bipolar_mass_below_threshold",
    "axis_budget_already_filled",
]


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
class ClaimAssumptionPull:
    claim_id: str
    assumption_id: str
    pull: float
    weight: float = 1.0
    lifecycle_stage: LifecycleStage = "growth"
    tam_structure: TamStructure = field(default_factory=dict)
    is_conditional: bool = False


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


@dataclass(frozen=True)
class AssumptionMassGateResult:
    assumption_id: str
    positive_mass: float
    negative_mass: float
    passes: bool


@dataclass(frozen=True)
class BipolarClaimMassGateResult:
    positive_mass: float
    negative_mass: float
    passes: bool


@dataclass(frozen=True)
class Type1CandidateComponentDiagnostic:
    component_index: int
    explained_variance_ratio: float
    cumulative_explained_variance_ratio: float
    loadings: Mapping[str, float]
    stability_score: float
    stage_c_mass_gate: BipolarClaimMassGateResult
    rejection_reason: Type1AxisRejectionReason | None
    promoted_axis: NarrativeAxis | None


@dataclass(frozen=True)
class Type1TensionAxisDiagnostics:
    assumption_mass_gates: tuple[AssumptionMassGateResult, ...]
    pca_explained_variance_ratios: tuple[float, ...]
    candidate_components: tuple[Type1CandidateComponentDiagnostic, ...]
    promoted_axes: tuple[NarrativeAxis, ...]


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


def evaluate_type1_assumption_mass_gates(
    pulls: Sequence[ClaimAssumptionPull],
    *,
    contested_mass_threshold: float = DEFAULT_CONTESTED_MASS_THRESHOLD,
) -> tuple[AssumptionMassGateResult, ...]:
    """Evaluate the Stage A bipolar mass gate for each assumption."""

    _validate_contested_mass_threshold(contested_mass_threshold)
    ordered_pulls = _ordered_claim_assumption_pulls(pulls)
    masses: dict[str, list[float]] = {}
    for pull in ordered_pulls:
        weighted_pull = _weighted_claim_assumption_pull(pull)
        positive_mass, negative_mass = masses.setdefault(pull.assumption_id, [0.0, 0.0])
        if weighted_pull > 0.0:
            positive_mass += weighted_pull
        elif weighted_pull < 0.0:
            negative_mass += abs(weighted_pull)
        masses[pull.assumption_id] = [positive_mass, negative_mass]

    return tuple(
        AssumptionMassGateResult(
            assumption_id=assumption_id,
            positive_mass=positive_mass,
            negative_mass=negative_mass,
            passes=(
                positive_mass >= contested_mass_threshold
                and negative_mass >= contested_mass_threshold
            ),
        )
        for assumption_id, (positive_mass, negative_mass) in sorted(masses.items())
    )


def generate_type1_tension_axes(
    pulls: Sequence[ClaimAssumptionPull],
    *,
    contested_mass_threshold: float = DEFAULT_CONTESTED_MASS_THRESHOLD,
    explained_variance_threshold: float = DEFAULT_EXPLAINED_VARIANCE_THRESHOLD,
    stability_threshold: float = DEFAULT_AXIS_STABILITY_THRESHOLD,
    max_axes: int = DEFAULT_MAX_AXES,
) -> tuple[NarrativeAxis, ...]:
    """Generate v6.1 Type-1 axes from gated, centered claim-by-assumption pulls."""

    return generate_type1_tension_axis_diagnostics(
        pulls,
        contested_mass_threshold=contested_mass_threshold,
        explained_variance_threshold=explained_variance_threshold,
        stability_threshold=stability_threshold,
        max_axes=max_axes,
    ).promoted_axes


def generate_type1_tension_axis_diagnostics(
    pulls: Sequence[ClaimAssumptionPull],
    *,
    contested_mass_threshold: float = DEFAULT_CONTESTED_MASS_THRESHOLD,
    explained_variance_threshold: float = DEFAULT_EXPLAINED_VARIANCE_THRESHOLD,
    stability_threshold: float = DEFAULT_AXIS_STABILITY_THRESHOLD,
    max_axes: int = DEFAULT_MAX_AXES,
) -> Type1TensionAxisDiagnostics:
    """Report every deterministic gate used to promote or reject Type-1 axes."""

    _validate_axis_controls(
        explained_variance_threshold=explained_variance_threshold,
        max_axes=max_axes,
    )
    _validate_contested_mass_threshold(contested_mass_threshold)
    _validate_stability_threshold(stability_threshold)
    ordered_pulls = _ordered_claim_assumption_pulls(pulls)
    gate_results = evaluate_type1_assumption_mass_gates(
        ordered_pulls,
        contested_mass_threshold=contested_mass_threshold,
    )
    retained_assumption_ids = tuple(
        result.assumption_id for result in gate_results if result.passes
    )
    if not retained_assumption_ids:
        return Type1TensionAxisDiagnostics(
            assumption_mass_gates=gate_results,
            pca_explained_variance_ratios=(),
            candidate_components=(),
            promoted_axes=(),
        )

    matrix, claim_ids, assumption_ids = _centered_claim_assumption_matrix(
        ordered_pulls,
        retained_assumption_ids,
    )
    if matrix.size == 0:
        return Type1TensionAxisDiagnostics(
            assumption_mass_gates=gate_results,
            pca_explained_variance_ratios=(),
            candidate_components=(),
            promoted_axes=(),
        )

    _, singular_values, right_singular_vectors = np.linalg.svd(
        matrix,
        full_matrices=False,
    )
    variances = np.square(singular_values)
    positive_variance_mask = variances > ZERO_VARIANCE_TOLERANCE
    positive_variances = variances[positive_variance_mask]
    if positive_variances.size == 0:
        return Type1TensionAxisDiagnostics(
            assumption_mass_gates=gate_results,
            pca_explained_variance_ratios=(),
            candidate_components=(),
            promoted_axes=(),
        )

    total_variance = float(np.sum(positive_variances))
    explained_variance_ratios = positive_variances / total_variance
    explained_variance_ratio_tuple = tuple(
        float(ratio) for ratio in explained_variance_ratios
    )
    candidate_axis_count = min(
        _axis_count_for_threshold(explained_variance_ratios, explained_variance_threshold),
        max_axes,
    )
    claim_weights = _claim_weights_by_id(ordered_pulls)

    axes: list[NarrativeAxis] = []
    candidate_components: list[Type1CandidateComponentDiagnostic] = []
    cumulative_explained_variance_ratios = np.cumsum(explained_variance_ratios)
    positive_component_indexes = np.flatnonzero(positive_variance_mask)
    for variance_index, component_index in enumerate(positive_component_indexes):
        loadings = _deterministic_loadings(
            assumption_ids,
            right_singular_vectors[component_index, :],
        )
        loading_vector = np.asarray(
            [loadings[assumption_id] for assumption_id in assumption_ids],
            dtype=np.float64,
        )
        stability_score = _axis_stability_score(matrix, loading_vector)
        scores = matrix @ loading_vector
        stage_c_mass_gate = _axis_bipolar_claim_mass_gate_result(
            claim_ids=claim_ids,
            claim_weights=claim_weights,
            scores=scores,
            contested_mass_threshold=contested_mass_threshold,
        )
        rejection_reason: Type1AxisRejectionReason | None = None
        promoted_axis: NarrativeAxis | None = None
        if len(axes) >= candidate_axis_count:
            rejection_reason = "axis_budget_already_filled"
        elif stability_score < stability_threshold:
            rejection_reason = "stability_below_threshold"
        elif not stage_c_mass_gate.passes:
            rejection_reason = "stage_c_bipolar_mass_below_threshold"
        else:
            promoted_axis = NarrativeAxis(
                axis_index=len(axes),
                explained_variance_ratio=float(explained_variance_ratios[variance_index]),
                loadings=loadings,
            )
            axes.append(promoted_axis)

        candidate_components.append(
            Type1CandidateComponentDiagnostic(
                component_index=int(component_index),
                explained_variance_ratio=float(explained_variance_ratios[variance_index]),
                cumulative_explained_variance_ratio=float(
                    cumulative_explained_variance_ratios[variance_index]
                ),
                loadings=loadings,
                stability_score=stability_score,
                stage_c_mass_gate=stage_c_mass_gate,
                rejection_reason=rejection_reason,
                promoted_axis=promoted_axis,
            )
        )

    return Type1TensionAxisDiagnostics(
        assumption_mass_gates=gate_results,
        pca_explained_variance_ratios=explained_variance_ratio_tuple,
        candidate_components=tuple(candidate_components),
        promoted_axes=tuple(axes),
    )


def build_type1_claim_assumption_pulls(
    claims: Sequence[Claim],
    *,
    stage: LifecycleStage,
    assumption_ids: Sequence[str] | None = None,
    tam_structure: TamStructure | None = None,
) -> tuple[ClaimAssumptionPull, ...]:
    """Project real claims into deterministic Type-1 claim-assumption pull rows."""

    selected_assumption_ids = _selected_type1_assumption_ids(assumption_ids)
    drivers = claims_to_economic_drivers(list(claims))
    if not drivers:
        return ()

    has_margin_recovery = any(
        driver.name == "gross_margin" and driver.direction == "INCREASE"
        for driver in drivers
    )
    measurement_tam_structure = {} if tam_structure is None else dict(tam_structure)

    rows: list[ClaimAssumptionPull] = []
    for driver in drivers:
        # Economic-driver compression happens before matrix construction, matching routing.
        factor_values: dict[str, float] = {
            factor_name: factor_shift(driver.claim, intensity, stage)
            for factor_name, intensity in _routing_for_driver(
                driver,
                has_margin_recovery=has_margin_recovery,
            ).items()
        }
        for assumption_id in selected_assumption_ids:
            pull_value = _type1_assumption_pull_value(assumption_id, factor_values)
            if abs(pull_value) <= ZERO_VARIANCE_TOLERANCE:
                continue
            rows.append(
                ClaimAssumptionPull(
                    claim_id=driver.claim.claim_id,
                    assumption_id=assumption_id,
                    pull=pull_value,
                    lifecycle_stage=stage,
                    tam_structure=measurement_tam_structure,
                )
            )

    return tuple(sorted(rows, key=lambda row: (row.assumption_id, row.claim_id)))


def generate_narrative_axes(
    pulls: Sequence[ClaimAssumptionPull] | Sequence[PullSignature],
    *,
    contested_mass_threshold: float = DEFAULT_CONTESTED_MASS_THRESHOLD,
    explained_variance_threshold: float = DEFAULT_EXPLAINED_VARIANCE_THRESHOLD,
    stability_threshold: float = DEFAULT_AXIS_STABILITY_THRESHOLD,
    max_axes: int = DEFAULT_MAX_AXES,
) -> tuple[NarrativeAxis, ...]:
    """Generate v6.1 Type-1 axes from claim-assumption pulls."""

    return generate_type1_tension_axes(
        _claim_assumption_pulls_for_public_entrypoint(pulls),
        contested_mass_threshold=contested_mass_threshold,
        explained_variance_threshold=explained_variance_threshold,
        stability_threshold=stability_threshold,
        max_axes=max_axes,
    )


def _claim_assumption_pulls_for_public_entrypoint(
    pulls: Sequence[ClaimAssumptionPull] | Sequence[PullSignature],
) -> tuple[ClaimAssumptionPull, ...]:
    claim_assumption_pulls: list[ClaimAssumptionPull] = []
    for pull in pulls:
        if isinstance(pull, PullSignature):
            raise ValueError(
                "generate_narrative_axes no longer accepts aggregated PullSignature "
                "inputs; pass ClaimAssumptionPull rows or call "
                "generate_type1_tension_axes"
            )
        claim_assumption_pulls.append(pull)
    return tuple(claim_assumption_pulls)


def _selected_type1_assumption_ids(
    assumption_ids: Sequence[str] | None,
) -> tuple[str, ...]:
    if assumption_ids is None:
        return tuple(sorted(LOADING))
    if not assumption_ids:
        raise ValueError("assumption_ids must be non-empty when provided")
    if any(not assumption_id.strip() for assumption_id in assumption_ids):
        raise ValueError("assumption_ids must be non-empty")
    duplicate_ids = {
        assumption_id for assumption_id in assumption_ids if assumption_ids.count(assumption_id) > 1
    }
    if duplicate_ids:
        ordered_duplicates = ", ".join(sorted(duplicate_ids))
        raise ValueError(f"duplicate assumption_ids: {ordered_duplicates}")
    unknown_ids = {
        assumption_id for assumption_id in assumption_ids if assumption_id not in LOADING
    }
    if unknown_ids:
        ordered_unknown_ids = ", ".join(sorted(unknown_ids))
        raise ValueError(f"unknown assumption_ids: {ordered_unknown_ids}")
    return tuple(assumption_ids)


def _type1_assumption_pull_value(
    assumption_id: str,
    factor_values: Mapping[str, float],
) -> float:
    pull = sum(
        factor_values[factor_name] * loading
        for factor_name, loading in LOADING.get(assumption_id, {}).items()
        if factor_name in factor_values
    )
    if not np.isfinite(pull):
        raise ValueError("claim-assumption pull values must be finite")
    return float(pull)


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


def _validate_contested_mass_threshold(contested_mass_threshold: float) -> None:
    if not np.isfinite(contested_mass_threshold) or contested_mass_threshold <= 0.0:
        raise ValueError("contested_mass_threshold must be finite and positive")


def _validate_stability_threshold(stability_threshold: float) -> None:
    if not np.isfinite(stability_threshold) or not (0.0 <= stability_threshold <= 1.0):
        raise ValueError("stability_threshold must be finite and in [0, 1]")


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


def _ordered_claim_assumption_pulls(
    pulls: Sequence[ClaimAssumptionPull],
) -> tuple[ClaimAssumptionPull, ...]:
    if not pulls:
        raise ValueError("claim-assumption pulls must be non-empty")

    seen_cells: set[tuple[str, str]] = set()
    claim_weights: dict[str, float] = {}
    first_pull = pulls[0]
    lifecycle_stage = first_pull.lifecycle_stage
    tam_structure = first_pull.tam_structure

    for pull in pulls:
        if not pull.claim_id.strip():
            raise ValueError("claim_id must be non-empty")
        if not pull.assumption_id.strip():
            raise ValueError("assumption_id must be non-empty")
        if not np.isfinite(pull.pull):
            raise ValueError("claim-assumption pull values must be finite")
        if not np.isfinite(pull.weight) or pull.weight <= 0.0:
            raise ValueError("claim-assumption pull weights must be finite and positive")
        if pull.lifecycle_stage != lifecycle_stage or pull.tam_structure != tam_structure:
            raise ValueError(
                "claim-assumption pulls must share one measurement axis "
                "(same lifecycle_stage and tam_structure)"
            )

        cell = (pull.claim_id, pull.assumption_id)
        if cell in seen_cells:
            raise ValueError("claim-assumption pulls must be unique per claim and assumption")
        seen_cells.add(cell)

        existing_weight = claim_weights.setdefault(pull.claim_id, pull.weight)
        if existing_weight != pull.weight:
            raise ValueError("claim-assumption pull weights must be consistent per claim")

    return tuple(sorted(pulls, key=lambda pull: (pull.assumption_id, pull.claim_id)))


def _centered_claim_assumption_matrix(
    pulls: Sequence[ClaimAssumptionPull],
    retained_assumption_ids: Sequence[str],
) -> tuple[NDArray[np.float64], tuple[str, ...], tuple[str, ...]]:
    retained_assumptions = set(retained_assumption_ids)
    retained_pulls = tuple(
        pull for pull in pulls if pull.assumption_id in retained_assumptions
    )
    if not retained_pulls:
        empty = np.empty((0, 0), dtype=np.float64)
        return empty, (), ()

    claim_ids = tuple(sorted({pull.claim_id for pull in retained_pulls}))
    assumption_ids = tuple(sorted(retained_assumptions))
    claim_index = {claim_id: index for index, claim_id in enumerate(claim_ids)}
    assumption_index = {
        assumption_id: index for index, assumption_id in enumerate(assumption_ids)
    }
    matrix = np.zeros((len(claim_ids), len(assumption_ids)), dtype=np.float64)

    for pull in retained_pulls:
        matrix[claim_index[pull.claim_id], assumption_index[pull.assumption_id]] = (
            _weighted_claim_assumption_pull(pull)
        )

    return matrix - np.mean(matrix, axis=0, keepdims=True), claim_ids, assumption_ids


def _weighted_claim_assumption_pull(pull: ClaimAssumptionPull) -> float:
    discount = CONDITIONAL_PULL_DISCOUNT if pull.is_conditional else 1.0
    weighted_pull = pull.pull * pull.weight * discount
    if not np.isfinite(weighted_pull):
        raise ValueError("weighted claim-assumption pull values must be finite")
    return float(weighted_pull)


def _axis_stability_score(
    matrix: NDArray[np.float64],
    axis_loadings: NDArray[np.float64],
    min_rows_for_stability: int = 2,
) -> float:
    """Return the conservative leave-one-out cosine for one candidate axis."""

    n_rows = matrix.shape[0]
    if n_rows < min_rows_for_stability + 1:
        return 0.0

    axis_norm = float(np.linalg.norm(axis_loadings))
    if axis_norm <= ZERO_VARIANCE_TOLERANCE:
        return 0.0

    min_cosine = 1.0
    for row_index in range(n_rows):
        loo_matrix = np.delete(matrix, row_index, axis=0)
        centered = loo_matrix - np.mean(loo_matrix, axis=0, keepdims=True)
        if centered.shape[0] < min_rows_for_stability or np.allclose(
            centered,
            0.0,
            atol=ZERO_VARIANCE_TOLERANCE,
        ):
            return 0.0

        _, loo_singular_values, loo_right_singular_vectors = np.linalg.svd(
            centered,
            full_matrices=False,
        )
        # Right singular vectors are always unit-norm, so a vector-norm filter is a
        # no-op that keeps null-space directions (singular value 0, zero variance).
        # Filter on the singular values instead: only variance-bearing components are
        # valid matches. Otherwise an axis that collapses in this fold matches the LOO
        # null space and is spuriously promoted as stable.
        valid_components = loo_singular_values**2 > ZERO_VARIANCE_TOLERANCE
        if not np.any(valid_components):
            return 0.0

        valid_vectors = loo_right_singular_vectors[valid_components]
        cosines = np.abs(valid_vectors @ axis_loadings) / axis_norm
        best_cosine = float(np.max(cosines))
        min_cosine = min(min_cosine, best_cosine)

    return float(np.clip(min_cosine, 0.0, 1.0))


def _claim_weights_by_id(
    pulls: Sequence[ClaimAssumptionPull],
) -> dict[str, float]:
    weights: dict[str, float] = {}
    for pull in pulls:
        weights[pull.claim_id] = pull.weight
    return weights


def _passes_axis_bipolar_mass_gate(
    *,
    claim_ids: Sequence[str],
    claim_weights: Mapping[str, float],
    scores: NDArray[np.float64],
    contested_mass_threshold: float,
) -> bool:
    return _axis_bipolar_claim_mass_gate_result(
        claim_ids=claim_ids,
        claim_weights=claim_weights,
        scores=scores,
        contested_mass_threshold=contested_mass_threshold,
    ).passes


def _axis_bipolar_claim_mass_gate_result(
    *,
    claim_ids: Sequence[str],
    claim_weights: Mapping[str, float],
    scores: NDArray[np.float64],
    contested_mass_threshold: float,
) -> BipolarClaimMassGateResult:
    positive_mass = 0.0
    negative_mass = 0.0
    for claim_id, score in zip(claim_ids, scores, strict=True):
        if score > ZERO_VARIANCE_TOLERANCE:
            positive_mass += claim_weights[claim_id]
        elif score < -ZERO_VARIANCE_TOLERANCE:
            negative_mass += claim_weights[claim_id]
    return BipolarClaimMassGateResult(
        positive_mass=positive_mass,
        negative_mass=negative_mass,
        passes=(
            positive_mass >= contested_mass_threshold
            and negative_mass >= contested_mass_threshold
        ),
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
