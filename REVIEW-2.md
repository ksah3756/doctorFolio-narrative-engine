# REVIEW-2: NVDA Narrative Spike (P1 Remediation)

- **Branch**: `feat/spike-monte-carlo-nvda`
- **Reviewer**: Claude
- **Reviewed commits (since REVIEW-1)**:
  - `9a640d5` Expose the NVDA review blockers as regressions (Red — P1 regression tests)
  - `0c9941b` Record the first Claude review before remediation (docs — `REVIEW-1.md` checked in)
  - `3fc4241` Restore reviewable semantics in the NVDA spike (Green — P1 fixes)
- **Verdict**: **APPROVED** — all P1 blockers resolved with regression tests; ready for PR and merge.

## DoD Status

| Item | Status | Evidence |
|---|---|---|
| `make verify` 100% | ✓ | ruff clean, mypy strict clean, **26 tests pass** (was 22), coverage **92.61%** (was 92.16%) |
| TDD time order (Red → Green) | ✓ | `9a640d5` (21:31:13) touched ONLY `tests/`; `3fc4241` (21:31:42) added `src/` fixes |
| Tidy First commit separation | ✓ | Regression-test commit (red) and remediation commit (green) cleanly split; review artifact lives in its own commit (`0c9941b`) |
| Scope adherence | ✓ | No new behavior beyond REVIEW-1 P1 scope |

## P1 Remediation Verification

### P1-1 — LOADING table unified ✓

**Fix delivered (`monte_carlo.py:17,170`)**:

```python
from dcf_engine.loading import LOADING, apply_constraints
...
def _shifted_mu(assumption, sampled_factors):
    loading = LOADING.get(assumption.name, {})
    ...
```

- 16-line duplicate map deleted
- Single source of truth restored
- `apply_factor_loadings` remains the canonical full-pipeline loader; `_shifted_mu` now reads from the same `LOADING` table

**Regression guard (`test_monte_carlo.py:80-88`)**:

```python
def test_shifted_mu_uses_canonical_financial_strength_loading() -> None:
    assumption = _assumption("WACC", 0.10, 0.01, "normal")
    shifted = _shifted_mu(assumption, {"FinancialStrength": 1.0})
    assert shifted == pytest.approx(
        assumption.base_mu
        + LOADING["WACC"]["FinancialStrength"] * assumption.shift_scale.center
    )
```

This test would have **failed** under the pre-remediation code (FinancialStrength was silently dropped from the WACC inline subset). It now passes — verifying the formerly dropped factor reaches the assumption.

**Side effect (expected)**: NVDA spike output shifted from `4.13T → 4.05T` median, `2.50T/7.90T → 2.53T/7.78T` P10/P90 band, because WACC and DEFAULT_PROBABILITY now consume their full set of factor loadings. Still well inside the test's `2.7T ≤ median ≤ 8.2T` band. Numbers updated in `README.md` and `docs/nvda-spike-report.md` in the same commit — good hygiene.

### P1-2 — TAM ↔ MC pairing fixed via accepted_indices ✓

**Fix delivered**:

- `MonteCarloResult` gains `accepted_indices: np.ndarray` field (`monte_carlo.py:34`)
- `mc_run` tracks the outer-loop index per accepted draw (`monte_carlo.py:68,85`)
- `nvda_spike.py:65`:
  ```python
  fair_values = _fair_values(tam_samples[mc_result.accepted_indices], mc_result.samples)
  ```
  — proper index-based gather, not a positional slice

**Regression guard (`test_nvda_spike.py:34-71`)**: monkeypatches `sample_tam_total` to return `[100, 200, 300, 400]`, `mc_run` to return `accepted_indices=[1, 3]`, and asserts `captured_tam[0] == np.array([200.0, 400.0])`. Verifies that under non-zero reject_rate the TAM ↔ MC pairing follows the accepted indices, not the truncated prefix.

Also verifies via `test_mc_run_reports_outer_indices_for_accepted_samples` that `accepted_indices` correctly identifies positions 1 and 2 when index 0 is rejected, with the new `pytest.warns(RuntimeWarning, match="High reject rate")` capture for the >30% calibration signal — the warning path is now actually exercised.

### P1-3 — Magic numbers named ✓

**Fix delivered (`nvda_spike.py:29-33`)**:

```python
NVDA_SPIKE_TERMINAL_MARGIN_FLOOR: Final = 0.05
# Spike-only Gordon proxy; replace with explicit forecast/terminal DCF in the full engine.
NVDA_SPIKE_FCFF_TERMINAL_MULTIPLE: Final = 1.70
NVDA_SPIKE_TERMINAL_GROWTH: Final = 0.035
NVDA_SPIKE_DISCOUNT_SPREAD_FLOOR: Final = 0.025
```

All four `_fair_values` literals replaced with named constants. Doc update adds a "Scope and Approximations" section to `docs/nvda-spike-report.md` explicitly stating "spike-only Gordon proxy" and that the full forecast-period DCF is deferred.

**Regression guard (`test_nvda_spike.py:74-81`)**: asserts both the constants exist with expected values AND the report string contains "spike-only Gordon proxy". Future inlining of these numbers or removal of the scope note would fail the test.

## Strengths in Remediation

- **TDD discipline retained under pressure**: `9a640d5` is tests-only, `3fc4241` is fix + small Green-phase adjustments. Each P1 has a dedicated regression test that would have failed on the original code. This is the textbook order.
- **Commit message intent**: `3fc4241` explicitly cites REVIEW-1, identifies each P1's root cause as "hidden divergence between the spike show and intended semantics", and adds a directive ("Do not add factor loading values outside loading.py without a regression test") — useful for future contributors.
- **Side-effect propagation done right**: the fair value numbers shifting from the P1-1 fix were proactively updated in README and report; no stale documentation left behind.
- **`pytest.warns` properly used** to capture the high-reject-rate RuntimeWarning — previously the warning path was untested code.

## P2 Status

The 9 P2 items from REVIEW-1 were **not addressed in this PR**, consistent with REVIEW-1's note that P2 items are "non-blocking but recommended in the same patch where possible". Scope-tight remediation is the correct call given the PR is a spike. Tracked for follow-up:

| # | Item | Suggested follow-up |
|---|---|---|
| P2-1 | `mc_iteration_with_validation` / `_iteration_with_rng` near-duplicate | Follow-up refactor commit |
| P2-2 | `classify_lifecycle` precedence + scoring rewrite | v5 §6 design patch + separate PR |
| P2-3 | `routing.py:66` saturation `0.3` magic number | Bundle with P2-4 |
| P2-4 | `shift_scale.center = 0.05` hardcoded | Bundle when `HISTORICAL_VOLATILITY_BY_STAGE` is implemented |
| P2-5 | `AssumptionState.constraints` heterogeneous keys | Bundle with TERMINAL_GROWTH activation |
| P2-6 | `lifecycle.py` young/decline coverage gap | Quick test PR before v5 §6 rewrite |
| P2-7 | `reliability_matches_source` validator untested | One-line test addition |
| P2-8 | `mc_iteration_with_validation` RNG contract docs | Docstring addition |
| P2-9 | Report v5 design doc reference | Partially addressed (Scope & Approximations added); full v5 link still missing |

Recommend opening a single follow-up issue covering P2-1, P2-3, P2-6, P2-7, P2-8 — these are quick wins. P2-2 belongs in the v5 §6 lifecycle scoring patch. P2-4 and P2-5 belong with the multi-stage / multi-ticker extension.

## Decision

**APPROVED**. PR creation and merge to `main` recommended.

Post-merge follow-ups:
1. Open issue "P2 nitpicks from REVIEW-1/2" listing the 9 items above
2. Resume v5 §6 lifecycle scoring patch discussion (parallel to spike work; no blocker on merge)
3. Spike is now the reference implementation against which v5 patches (§6, §16) can be validated
