.PHONY: format lint test ci install-hooks

format:
	black .
	isort .

lint:
	black --check .
	isort --check-only .
	ruff check .
	mypy .

test:
	pytest tests/unit/ -v

ci: lint test

install-hooks:
	pre-commit install
