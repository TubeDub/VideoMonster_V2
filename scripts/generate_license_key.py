#!/usr/bin/env python3
"""Генерация лицензионных ключей VideoMonster V2 (только для владельца)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from engines.license_manager import generate_key  # noqa: E402

TYPES = ["TEST-7", "TEST-30", "PREMIUM-WEEK", "PREMIUM-MONTH", "PREMIUM-YEAR", "LIFETIME"]


def main() -> int:
    p = argparse.ArgumentParser(description="Generate VideoMonster license key")
    p.add_argument("type", choices=TYPES, help="Key type")
    p.add_argument("-n", type=int, default=1, help="How many keys")
    args = p.parse_args()

    print(f"=== {args.type} ===")
    for _ in range(args.n):
        print(generate_key(args.type))
    print("\nФормат: VM-XXXX-XXXX-XXXX")
    print("Секрет: data/license_secret.txt или VM_LICENSE_SECRET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
