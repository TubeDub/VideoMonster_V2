#!/usr/bin/env python
"""CLI: P5 DSAL George Lucas benchmark (TZ v4.0).

Examples:
  python scripts/run_p5_dsal_benchmark.py
  python scripts/run_p5_dsal_benchmark.py --out output/p5_dsal_benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="P5 DSAL Benchmark")
    parser.add_argument(
        "--golden",
        type=Path,
        default=ROOT / "tests" / "golden" / "dub" / "george_lucas_en_uk_20.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "p5_dsal_benchmark",
    )
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="Allow LLM enhance (default: LLM off for governance baseline)",
    )
    args = parser.parse_args()

    from engines.dsal.benchmark import run_dsal_benchmark

    report = run_dsal_benchmark(
        golden_path=args.golden,
        out_dir=args.out,
        llm_off=not args.allow_llm,
        allow_llm=bool(args.allow_llm),
    )
    print(
        json.dumps(
            {
                "ok": report.ok,
                "suite": report.suite,
                "llm_off": report.llm_off,
                "path": report.path,
                "metrics": report.metrics,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    for g in report.gates:
        mark = "PASS" if g.ok else "FAIL"
        print(f"  [{mark}] {g.name}: {g.detail}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
