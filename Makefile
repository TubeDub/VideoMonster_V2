.PHONY: help install install-dev run test lint zip clean harden harden-long certify dsal-bench

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
	@echo "  make harden       - P16 Production Hardening (fast)"
	@echo "  make harden-long  - P16 long-run 30 min sample"
	@echo "  make dsal-bench   - P5 DSAL George Lucas benchmark (LLM off)"
	@echo "  make certify      - P17 Release Certificate (+ DSAL P5)"
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

harden:
	VM_DEV_MODE=1 $(PY) scripts/run_p16_hardening.py --long-run-sec 5

harden-long:
	VM_DEV_MODE=1 $(PY) scripts/run_p16_hardening.py --long-run-sec 1800 --no-pytest

dsal-bench:
	VM_DEV_MODE=1 $(PY) scripts/run_p5_dsal_benchmark.py

certify:
	VM_DEV_MODE=1 $(PY) scripts/run_p17_certify.py --p16-long-run-sec 2

zip:
	$(PY) scripts/run_master_checks.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache 2>/dev/null || true
