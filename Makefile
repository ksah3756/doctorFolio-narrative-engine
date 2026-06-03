.PHONY: install verify lint typecheck test test-fast clean

install:
	.venv/bin/python -m pip install -e '.[dev]'

verify: lint typecheck test

lint:
	.venv/bin/ruff check --no-cache src tests

typecheck:
	.venv/bin/mypy --cache-dir=/tmp/dcf-narrative-engine-mypy-cache src tests

test:
	COVERAGE_FILE=/tmp/dcf-narrative-engine.coverage .venv/bin/pytest

test-fast:
	COVERAGE_FILE=/tmp/dcf-narrative-engine.coverage .venv/bin/pytest -x --ff

clean:
	rm -rf .ruff_cache .mypy_cache .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
