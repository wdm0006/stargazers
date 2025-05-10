PYTHON ?= python3

.PHONY: install test lint demo

# Install all dependencies for development
install:
	uv pip install -e .[dev]

# Run all tests
test:
	uv run pytest -s

# format with ruff
format:
	uv run ruff format .

# Lint and format the codebase with ruff
lint:
	uv run ruff check --fix .

# Demo: install the package and run it for wdm0006/pygeohash
demo:
	uv pip install -e .[dev]
	uv run stargazers repos wdm0006/elote
	uv run stargazers forkers wdm0006/elote
	uv run stargazers account-trend wdm0006 --include-repo scikit-learn-contrib/category_encoders
	uv run stargazers plot --file wdm0006_account_stars_by_day.csv --type account-trend --title "Demo Plot for wdm0006"