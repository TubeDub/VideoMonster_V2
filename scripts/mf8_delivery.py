#!/usr/bin/env python3
"""MF8 — run Meaning Fit suites, flag matrix, build Desktop zip (separate from PSA)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "mf8_delivery"
REPORT = OUT / "MF8_DELIVERY_REPORT.json"
REPORT_MD = OUT / "MF8_DELIVERY_REPORT.md"

MF_DELIVERY_PATHS = [
    "engines/meaning_fit/__init__.py",
    "engines/meaning_fit/flags.py",
    "engines/meaning_fit/types.py",
    "engines/meaning_fit/exceptions.py",
    "engines/meaning_fit/skeleton.py",
    "engines/meaning_fit/duration_predictor.py",
    "engines/meaning_fit/semantic_shorten.py",
    "engines/meaning_fit/semantic_expand.py",
    "engines/meaning_fit/score_select.py",
    "engines/meaning_fit/orchestrator.py",
    "engines/meaning_fit/diagnostics.py",
    "data/feature_flags.json",
    "api/auto_dub_api.py",
    "tests/fixtures/mf0_goat.json",
    "tests/test_mf0_goat.py",
    "tests/test_mf1_flags_skeleton.py",
    "tests/test_mf2_duration_predictor.py",
    "tests/test_mf3_semantic_shorten.py",
    "tests/test_mf4_semantic_expand.py",
    "tests/test_mf5_score_select.py",
    "tests/test_mf6_before_lock.py",
    "tests/test_mf7_honest_reasons.py",
    "tests/test_mf8_acceptance.py",
    "scripts/mf8_delivery.py",
]

SUITES = [
    ("mf0_goat", "tests/test_mf0_goat.py", "3 RED (dump)"),
    ("mf1_flags", "tests/test_mf1_flags_skeleton.py", "n/a"),
    ("mf2_duration", "tests/test_mf2_duration_predictor.py", "n/a"),
    ("mf3_shorten", "tests/test_mf3_semantic_shorten.py", "n/a"),
    ("mf4_expand", "tests/test_mf4_semantic_expand.py", "n/a"),
    ("mf5_score", "tests/test_mf5_score_select.py", "n/a"),
    ("mf6_lock_order", "tests/test_mf6_before_lock.py", "n/a"),
    ("mf7_reasons", "tests/test_mf7_honest_reasons.py", "n/a"),
    ("mf8_acceptance", "tests/test_mf8_acceptance.py", "n/a"),
]

FLAGS = [
    "VM_FLAG_MEANING_FIT",
    "VM_FLAG_MEANING_FIT_SHORTEN",
    "VM_FLAG_MEANING_FIT_EXPAND",
    "VM_FLAG_MEANING_FIT_BEFORE_LOCK",
]


def run_pytest(paths: list[str], env: dict[str, str] | None = None) -> dict:
    junit = Path(tempfile.gettempdir()) / f"mf8_junit_{os.getpid()}_{time.time_ns()}.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-q",
        "--tb=no",
        f"--junitxml={junit}",
    ]
    full_env = os.environ.copy()
    full_env["PYTHONIOENCODING"] = "utf-8"
    full_env["PYTHONUTF8"] = "1"
    if env:
        full_env.update(env)
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
    )
    elapsed = time.perf_counter() - t0
    passed = failed = 0
    if junit.is_file():
        try:
            root = ET.parse(junit).getroot()
            suites = list(root) if root.tag == "testsuites" else [root]
            for suite in suites:
                if suite.tag != "testsuite":
                    continue
                tests = int(suite.attrib.get("tests", 0))
                fails = int(suite.attrib.get("failures", 0))
                errors = int(suite.attrib.get("errors", 0))
                skipped = int(suite.attrib.get("skipped", 0))
                failed += fails + errors
                passed += max(0, tests - fails - errors - skipped)
        except Exception:
            pass
        try:
            junit.unlink(missing_ok=True)
        except Exception:
            pass
    return {
        "exit_code": proc.returncode,
        "passed": passed,
        "failed": failed,
        "seconds": round(elapsed, 3),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    suite_rows = []
    print("=== MF8 suites ===")
    for name, path, before in SUITES:
        print(f"  running {name}...")
        r = run_pytest([path])
        status = (
            "PASS"
            if r["exit_code"] == 0 and r["failed"] == 0 and r["passed"] > 0
            else "FAIL"
        )
        suite_rows.append(
            {
                "suite": name,
                "before": before,
                "after": f"{r['passed']} passed, {r['failed']} failed",
                "delta": status,
                "seconds": r["seconds"],
            }
        )
        print(f"    -> {status} ({r['passed']}p/{r['failed']}f)")

    print("=== Flag OFF smoke ===")
    flags = []
    base = {f: "0" for f in FLAGS}
    r = run_pytest(["tests/test_mf1_flags_skeleton.py"], env=base)
    flags.append(
        {
            "mode": "all_MF_flags_OFF",
            "ok": r["exit_code"] == 0,
            "passed": r["passed"],
            "failed": r["failed"],
        }
    )
    for flag in FLAGS:
        env = {f: "1" for f in FLAGS}
        env[flag] = "0"
        r = run_pytest(
            ["tests/test_mf1_flags_skeleton.py::test_mf1_flag_off_legacy_passthrough_ok"],
            env=env,
        )
        flags.append(
            {
                "mode": f"{flag}_OFF_independent",
                "ok": r["exit_code"] == 0,
                "passed": r["passed"],
                "failed": r["failed"],
            }
        )
        print(f"  {flag}_OFF: {'OK' if r['exit_code']==0 else 'FAIL'}")

    # PSA smoke (identity) — not mixed into MF zip logic, just report
    psa = run_pytest(
        ["tests/test_mf8_acceptance.py::test_mf8_psa_smoke_identity_guard"],
        env={f: "1" for f in FLAGS} | {"VM_FLAG_IDENTITY_GUARD": "1"},
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_path = OUT / f"MeaningFit_MF1-MF7_Desktop_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in MF_DELIVERY_PATHS:
            src = ROOT / rel
            if src.is_file():
                zf.write(src, arcname=rel)
        zf.writestr(
            "MF_MANIFEST.json",
            json.dumps(
                {
                    "phase": "MF8",
                    "includes": "MF1–MF7 Meaning Fit package",
                    "separate_from": "PSA zip",
                    "desktop_drop": str(Path.home() / "Desktop" / "MeaningFit_MF1-MF7"),
                    "files": [p for p in MF_DELIVERY_PATHS if (ROOT / p).is_file()],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    report = {
        "phase": "MF8",
        "suites": suite_rows,
        "flags_matrix": flags,
        "psa_smoke": {
            "ok": psa["exit_code"] == 0,
            "passed": psa["passed"],
            "failed": psa["failed"],
        },
        "zip_path": str(zip_path),
        "desktop_instructions": (
            f"Copy zip to Desktop and unpack into "
            f"{Path.home() / 'Desktop' / 'MeaningFit_MF1-MF7'} "
            f"(separate from PSA delivery)."
        ),
        "next": "DONE",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# MF8 Delivery Report — Meaning Fit",
        "",
        "| suite | before | after | delta |",
        "|---|---|---|---|",
    ]
    for s in suite_rows:
        lines.append(f"| {s['suite']} | {s['before']} | {s['after']} | {s['delta']} |")
    lines += [
        "",
        "## Flags OFF",
        "",
    ]
    for f in flags:
        lines.append(f"- {f['mode']}: {'OK' if f['ok'] else 'FAIL'}")
    lines += [
        "",
        f"## Final zip",
        "",
        f"`{zip_path}`",
        "",
        f"Desktop: `{Path.home() / 'Desktop' / 'MeaningFit_MF1-MF7'}`",
        "",
        "NEXT: DONE",
        "",
    ]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.write(REPORT, arcname="output/mf8_delivery/MF8_DELIVERY_REPORT.json")
        zf.write(REPORT_MD, arcname="output/mf8_delivery/MF8_DELIVERY_REPORT.md")

    print(f"Zip: {zip_path}")
    print(f"Report: {REPORT_MD}")
    hard = any(s["delta"] == "FAIL" for s in suite_rows) or any(not f["ok"] for f in flags)
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
