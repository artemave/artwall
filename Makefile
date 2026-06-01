.DEFAULT_GOAL := check
.PHONY: check lint typecheck test coverage install-dev

# Run every check: lint, typecheck, and tests under 100%-coverage gate.
check: lint typecheck coverage

lint:
	python3 -m ruff check .

typecheck:
	python3 -m mypy

test:
	python3 -m unittest discover -s tests

coverage:
	python3 -m coverage run -m unittest discover -s tests
	python3 -m coverage report

install-dev:
	python3 -m pip install -r requirements-dev.txt
