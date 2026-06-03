# dcf-narrative-engine

Narrative-driven DCF valuation engine that maps qualitative company claims into quantitative valuation input distributions.

## Quick Start

```bash
uv sync --all-extras
make verify
uv run python -m dcf_engine.nvda_spike
```

Example output:

```text
NVDA spike fair value median: 3.10T USD
P10/P90 band: 1.72T / 5.21T USD
reject_rate: 0.00%
```

