#!/usr/bin/env python
"""CLI: Production Hardening P16.

Examples:
  python scripts/run_p16_hardening.py
  python scripts/run_p16_hardening.py --long-run-sec 30
  python scripts/run_p16_hardening.py --long-run-sec 28800   # 8h
  python scripts/run_p16_hardening.py --no-pytest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="P16 Production Hardening")
    parser.add_argument("--long-run-sec", type=float, default=5.0)
    parser.add_argument("--no-pytest", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "p16_hardening" / "checklist.json",
    )
    args = parser.parse_args()

    from engines.production_hardening.checklist import run_release_checklist

    result = run_release_checklist(
        include_pytest=not args.no_pytest,
        long_run_sec=args.long_run_sec,
        work_dir=args.out.parent,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"ok": result.ok, "items": len(result.items), "out": str(args.out)}, indent=2))
    for item in result.items:
        mark = "PASS" if item.ok else "FAIL"
        print(f"  [{mark}] {item.name}: {item.detail[:120]}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
