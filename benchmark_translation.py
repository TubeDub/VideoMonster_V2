#!/usr/bin/env python
"""Run MT benchmark from project root — Task №14."""
from scripts.benchmark_translation import run_benchmark
import sys

if __name__ == "__main__":
    inc_nllb = "--nllb" in sys.argv
    raise SystemExit(run_benchmark(include_nllb=inc_nllb))
