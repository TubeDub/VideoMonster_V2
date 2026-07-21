#!/usr/bin/env python
"""CLI: P17 Quality Certification & Release Governance.

Examples:
  python scripts/run_p17_certify.py
  python scripts/run_p17_certify.py --promote
  python scripts/run_p17_certify.py --no-p16
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="P17 Release Certificate")
    parser.add_argument("--promote", action="store_true", help="Promote Golden Release if approved")
    parser.add_argument("--no-p16", action="store_true", help="Skip nested P16 checklist")
    parser.add_argument("--p16-long-run-sec", type=float, default=1.5)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "p17_certification" / "release_certificate.json",
    )
    args = parser.parse_args()

    from engines.release_governance.certificate import issue_release_certificate

    cert = issue_release_certificate(
        work_dir=args.out.parent,
        promote_if_approved=args.promote,
        include_p16=not args.no_p16,
        p16_long_run_sec=args.p16_long_run_sec,
    )
    print(
        json.dumps(
            {
                "status": cert.status,
                "approved": cert.approved,
                "system_version": cert.system_version,
                "path": cert.path,
            },
            indent=2,
        )
    )
    for section in cert.sections:
        mark = "PASS" if section.ok else "FAIL"
        print(f"  [{mark}] {section.name}: {section.detail[:140]}")
    if cert.known_limitations:
        print("Known limitations:")
        for lim in cert.known_limitations:
            print(f"  - {lim}")
    return 0 if cert.approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
