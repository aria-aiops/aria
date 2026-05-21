VENV := .venv
PY   := $(VENV)/bin/python
BIN  := $(VENV)/bin

.PHONY: format lint test ci install-hooks venv

venv:
	python3 -m venv $(VENV)
	$(BIN)/pip install -q -r requirements.txt black isort ruff mypy pre-commit

format:
	$(BIN)/black .
	$(BIN)/isort .

lint:
	$(BIN)/black --check .
	$(BIN)/isort --check-only .
	$(BIN)/ruff check .
	$(BIN)/mypy .

test:
	$(BIN)/pytest tests/unit/ -v

ci: lint test

install-hooks:
	$(BIN)/pre-commit install
