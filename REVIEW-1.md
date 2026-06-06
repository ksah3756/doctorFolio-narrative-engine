# REVIEW-1: NVDA Narrative Spike

- **Branch**: `feat/spike-monte-carlo-nvda`
- **Reviewer**: Claude
- **Reviewed commits**:
  - `95b8ff0` Specify the NVDA spike behavior before implementation (Red)
  - `c41fcd5` Make the NVDA narrative spike reviewable (Green)
  - `df58f57` Clarify why the NVDA spike stays deterministic (Refactor)
- **Verdict**: **REQUEST CHANGES** — P1 blockers must be resolved before merge.

## DoD Status

| Item | Status | Evidence |
|---|---|---|
| `make verify` 100% | ✓ | ruff clean, mypy strict clean, 22 tests pass, coverage 92.16% (gate 80%) |
| TDD time order (Red → Green) | ✓ | 95b8ff0 (22:17) touched ONLY `tests/`; c41fcd5 (22:32) added `src/` |
| Tidy First commit separation | △ | Green commit also tweaked `tests/*` assertions (+15 lines across 8 files) to match implemented signatures — borderline acceptable in TDD Green phase, but flagged for future discipline |
| Reporting (Plan/Files/Commands/Tests/Risks) | ✓ | Commit messages carry intent + scope-risk + tested/not-tested annotations |
| §18 scope adherence | ✓ | NVDA only, growth stage, hardcoded claims, N=1000, normal regime, ingestion/LLM/Neo4j/UI all out of scope |

---

## P1 Blockers

### P1-1 — LOADING table duplicated; Monte Carlo path silently uses a subset

`src/dcf_engine/loading.py:26-57` defines the canonical `LOADING` map (6 assumptions × up to 6 factors). `src/dcf_engine/monte_carlo.py:162-175` re-declares the same map inline inside `_shifted_mu` but with **fewer factors** for several assumptions.

Concrete diffs:

| Assumption | `LOADING` (loading.py) | `_shifted_mu` (monte_carlo.py) | Silently dropped |
|---|---|---|---|
| `REVENUE_CAGR` | Demand 0.7, CompAdv 0.4, Macro 0.2, **Exec 0.2, FinStr 0.1** | Demand 0.7, CompAdv 0.4, Macro 0.2 | ExecutionQuality, FinancialStrength |
| `OPERATING_MARGIN` | Demand 0.2, CompAdv 0.5, OpEff 0.6, Macro 0.1, **Exec 0.3, FinStr 0.1** | same minus Exec/FinStr | ExecutionQuality, FinancialStrength |
| `WACC` | **OpEff −0.1**, Macro −0.7, **FinStr −0.2** | Macro −0.7 only | OperatingEfficiency, FinancialStrength |
| `DEFAULT_PROBABILITY` | OpEff −0.1, Macro −0.2, **Exec −0.2, FinStr −0.9** | Macro −0.2 only | OperatingEfficiency, ExecutionQuality, FinancialStrength |

The MC path calls `_shifted_mu` (`monte_carlo.py:111`). `apply_factor_loadings` from `loading.py:60` is **dead production code** — `grep` confirms it's referenced only from `tests/test_routing_loading.py:27`.

**Impact**: Tests pass against the correct `LOADING` table while the production valuation path uses a degraded subset. WACC and DEFAULT_PROBABILITY are especially affected — DEFAULT_PROBABILITY loses 90% of its FinancialStrength signal, which is the dominant factor by design. NVDA spike output is biased low-variance for these assumptions.

**Fix (pick one)**:
- Preferred: delete `_shifted_mu` and call `apply_factor_loadings` from `mc_iteration`. Single source of truth.
- Minimum: have `_shifted_mu` import `LOADING` from `loading.py` and use it directly.

Either way, add a test that asserts `_shifted_mu` for `WACC` consumes the FinancialStrength factor (regression guard).

### P1-2 — TAM samples desync from MC samples under rejection

`src/dcf_engine/nvda_spike.py:60-62`:

```python
fair_values = _fair_values(
    tam_samples[: len(mc_result.samples["REVENUE_CAGR"])], mc_result.samples
)
```

TAM is sampled `iterations` times in advance (line 47). `mc_run` rejects some draws; accepted count = `iterations - dropped`. The slice `tam_samples[:accepted]` takes the **first K** TAM samples, but those weren't paired with the K accepted MC iterations — rejected MC draws were interspersed.

**Impact**: This breaks the factor-first correlation guarantee (design v5 §13): a single underlying draw should propagate through TAM, factors, and assumptions consistently. Under the current code, TAM[i] is paired with MC[i] regardless of which MC[i'] was rejected. Currently `reject_rate=0%` so this doesn't manifest, but the moment a stress regime or tighter constraint pushes rejections, TAM↔assumption pairing silently desyncs.

**Fix**: Move TAM sampling **inside** the rejection loop (in `mc_iteration`) so each accepted iteration carries a TAM value that was drawn together with the assumption shocks. Alternative: track which iteration indices were accepted and select TAM by index, not by truncation.

### P1-3 — Magic numbers in fair-value path

`src/dcf_engine/nvda_spike.py:151-155`:

```python
terminal_margin = np.maximum(samples["OPERATING_MARGIN"], 0.05)
fcff = revenue * terminal_margin * (1 - samples["TAX_RATE"]) * 1.70
discount_spread = np.maximum(samples["WACC"] - 0.035, 0.025)
going_concern = fcff / discount_spread
```

Four unnamed constants on the critical valuation path: `0.05` (margin floor), `1.70` (FCFF multiplier — appears to substitute for a Gordon terminal value), `0.035` (terminal growth proxy), `0.025` (discount spread floor).

`AGENTS.md §4 Anti-Patterns`: *"magic number (모든 상수는 명명된 테이블)"*. Design v5 §4 specifically rejects unnamed numerics in valuation logic.

**Fix**: Hoist to module-level `Final` constants with a one-line intent comment each:

```python
NVDA_SPIKE_TERMINAL_MARGIN_FLOOR: Final = 0.05
NVDA_SPIKE_FCFF_TERMINAL_MULTIPLE: Final = 1.70  # placeholder Gordon proxy, replace in full DCF
NVDA_SPIKE_TERMINAL_GROWTH: Final = 0.035
NVDA_SPIKE_DISCOUNT_SPREAD_FLOOR: Final = 0.025
```

Also document in `nvda-spike-report.md` that these are spike-only Gordon approximations, not the v5 design's full DCF.

---

## P2 Improvements

### P2-1 — `mc_iteration_with_validation` and `_iteration_with_rng` are near-duplicates

`monte_carlo.py:36-57` and `124-145` are essentially the same loop, differing only in RNG ownership. Unify by having `mc_iteration_with_validation` call `_iteration_with_rng` with a fresh `np.random.default_rng(config.seed)`. Removes 22 LOC of duplicated control flow.

### P2-2 — `classify_lifecycle` operator precedence is implicit

`lifecycle.py:32-37`:

```python
if (
    company.years_since_ipo < 3
    or company.revenue_cagr_3y > 0.40
    and company.operating_margin < 0
    and company.fcfe_recent < 0
):
```

Python parses this as `A or (B and C and D)`. Probably intentional, but reader has to recall precedence. Add explicit parens.

Note: the broader scoring-based classifier discussion (your message earlier today proposing 4-axis scoring + hysteresis) is **out of scope for this PR**. It belongs in a v5 §6 design patch and a follow-up issue. This PR implements the original v5 sketch faithfully.

### P2-3 — Saturation magic number in routing

`routing.py:66`: `1 / (1 + same_direction_counts[...] * 0.3)`. Hoist `0.3` to `SAME_DIRECTION_SATURATION_RATE: Final = 0.3` at module top with comment explaining the cap intent.

### P2-4 — `shift_scale.center = 0.05` repeated with no source

`nvda_spike.py:206` and every test fixture hard-codes `ScaleSpec(center=0.05, uncertainty=0.0)`. Design v5 specified `HISTORICAL_VOLATILITY_BY_STAGE` table as the source. The spike defers calibration, which is fine for §18, but the value should be:
- Named (`NVDA_GROWTH_SHIFT_SCALE_CENTER`)
- Sourced (one-line comment: "NVDA 5y revenue σ approx; replace with HISTORICAL_VOLATILITY_BY_STAGE in v5 patch")

### P2-5 — `AssumptionState.constraints` heterogeneous keys

`loading.py:121,123` reads `constraints.get("risk_free_rate", 0.045)`. `nvda_spike.py:207` only sets `{"low": 0.0, "high": 1.0}` — `risk_free_rate` is never set, so the default `0.045` is always taken. This is invisible breakage waiting to happen.

Options:
- Promote to a typed `Constraints` dataclass with explicit fields, or
- Pass `risk_free_rate` explicitly per assumption that needs it (TERMINAL_GROWTH, WACC), or
- Document the default is intentional and add a test that asserts it.

For this PR: at minimum add a comment that `risk_free_rate` falls back to 0.045 by design.

### P2-6 — Test coverage gap on lifecycle decline/young branches

`tests/test_lifecycle.py` exercises growth + mature only. `coverage` shows `lifecycle.py:38,40` uncovered — those are the young (`years_since_ipo < 3`) and decline branches. Even if the classifier will be replaced (see P2-2), add two tests to prevent silent breakage during the refactor.

### P2-7 — Reliability mismatch validator untested

`claim.py:77-82` rejects `SourceRef` when `source_reliability` disagrees with `SOURCE_RELIABILITY[content_source]` by more than 1e-9. No test asserts the error path. Add `test_source_ref_rejects_reliability_inconsistent_with_table` raising `ValidationError`.

### P2-8 — `mc_iteration_with_validation` RNG reset behavior

`monte_carlo.py:45` resets `rng = np.random.default_rng(config.seed)` every call. If a caller invokes it multiple times with the same config, all calls return identical samples. Currently only `test_rejection_sampling_drops_unrealistic_imputed_roic` calls it, and that test relies on this determinism, so it's fine — but the function's contract should be documented (one-shot deterministic) or the RNG should be injected to make ownership explicit.

### P2-9 — `docs/nvda-spike-report.md` lacks v5 reference

The report cites Section 18 outcomes but doesn't link `design-v5-2026-06-03.md` or note that the 1.70 multiplier is a spike-only Gordon proxy. One-paragraph "scope & approximations" section would close the loop for future readers.

---

## Strengths

- **TDD discipline verified**: Red commit `95b8ff0` touched only `tests/` (8 files, 414 insertions, zero `src/`). Green added implementation. Time order strict.
- **Property-based tests** for distribution moment inversion (`test_distributions.py:18-43`) — lognormal mean+std and median+std round-trips. Exactly the invariant testing AGENTS.md §4.3 calls for.
- **Prompt injection sanitization** (`claim.py:63-67`, three regex patterns) is a proactive defense not even in the v5 spec — good preemptive hardening.
- **Discovery vs content source split** correctly implemented (`SourceRef.discovery_channel` distinct from `content_source`); reliability looked up from `content_source` (`claim.py:146-147`). Matches design v5 §7.
- **C1 effective_n** correctly uses decay-weighted sum (`factor.py:63`), not raw count.
- **C2 ROIC = margin × (1 − tax) × S/C** correctly implemented (`validation.py:13-17`).
- **B1 rejection sampling** bounded at `max_resample=5` with `RuntimeWarning` when `reject_rate > 30%` (`monte_carlo.py:87-92`). Matches v5 §9.
- **B3 Lognormal median-based** added alongside moment-based (`distributions.py:45-49`).
- **Bottom-up TAM** multiplicand product for 4 NVDA segments via TOML — no hardcoded TAM constant. `multiplicands.toml` is editable without code changes.
- **Distress-adjusted EV→Equity bridge** correctly applies default probability at firm value level only (`bridge.py:20-32`), no double-counting.
- **Coverage 92.16%**, ruff strict clean, mypy strict clean.
- **Pydantic v2 frozen models** throughout (`Claim`, `SourceRef`, `ExtractionQuality`) — immutable inputs prevent silent mutation bugs.
- **PEP 695 `type` aliases** (`type ClaimSubject = Literal[...]`) cleanly express the finite-state design intent.

---

## What to fix before re-notification

1. **P1-1**: Unify LOADING source of truth (loading.py wins). Add regression test for `_shifted_mu` consuming all factors in the canonical map.
2. **P1-2**: Move TAM sampling inside the rejection loop so factor-first correlation holds under rejection. Add a test that simulates non-zero reject_rate and asserts TAM↔assumption pairing.
3. **P1-3**: Name the four magic numbers in `_fair_values` as `Final` constants and add a "spike-only Gordon proxy" note to `nvda-spike-report.md`.

P2 items are non-blocking but recommended in the same patch where possible.

When fixes are in, run `make verify`, confirm `git status` clean, and re-notify via `discord-review-notify` for REVIEW-2.
