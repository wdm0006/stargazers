PYTHON ?= python3

.PHONY: install test lint demo

# Install all dependencies for development
install:
	uv pip install -e .[dev]

# Run all tests
test:
	uv run pytest

# Lint and format the codebase with ruff
lint:
	uv run ruff check --fix stargazers/ tests/
	uv run ruff format stargazers/ tests/

# Demo: install the package and run it for wdm0006/pygeohash
demo:
	uv pip install -e .[dev]
	uv run stargazers wdm0006/pygeohash 