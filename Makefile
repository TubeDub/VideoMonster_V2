.PHONY: help install install-dev run test lint zip clean

PY ?= python
ROOT := $(CURDIR)

help:
	@echo "TubeDub dev commands:"
	@echo "  make install      - pip install requirements"
	@echo "  make install-dev  - pip install + pytest + ruff"
	@echo "  make run          - start Flask (app.py)"
	@echo "  make test         - pytest tests/"
	@echo "  make test-all     - pytest + all scripts/test_*.py"
	@echo "  make lint         - ruff check"
	@echo "  make zip          - release ZIP (no cache/models)"
	@echo "  make clean        - remove __pycache__, .pytest_cache"

install:
	$(PY) -m pip install -r requirements.txt

install-dev: install
	$(PY) -m pip install pytest ruff

run:
	$(PY) app.py

test:
	VM_DEV_MODE=1 VM_PREPARE_WARMUP=0 $(PY) -m pytest tests/ -q

test-all:
	@for f in scripts/test_*.py; do echo "==> $$f"; $(PY) "$$f" || exit 1; done

lint:
	ruff check api engines tests

zip:
	$(PY) scripts/run_master_checks.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache 2>/dev/null || true
