# NVDA Spike Report

## Section 18.4 Results

- Data Center bottom-up TAM participates in a total TAM distribution centered near 432B USD.
- Deterministic spike output: median fair value 4.05T USD, P10/P90 band 2.53T / 7.78T USD,
  reject_rate 0.00%.
- Baseline fair value median target range: 2.7T-8.2T USD, matching +/-50% of the
  5.435T USD NVDA market-cap sanity check on 2026-06-03.
- Data Center revenue growth claim increases REVENUE_CAGR mu.
- COST_SIGNAL INCREASE decreases OPERATING_MARGIN mu.
- reject_rate target: below 30%; actual 0.00%.
- Seed: 20260603, iterations: 1,000.

## Scope and Approximations

- The fair-value path uses a spike-only Gordon proxy rather than the full v5 DCF:
  a named terminal margin floor, FCFF terminal multiple, terminal growth proxy, and
  discount-spread floor preserve a reviewable valuation distribution until the
  full forecast-period DCF is implemented.
