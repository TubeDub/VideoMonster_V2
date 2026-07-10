"""Generate WORK_REPORT for Language Intelligence v2."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TESTS = [
    "scripts/test_language_intelligence_v2.py",
    "scripts/test_language_intelligence.py",
]


def main() -> int:
    results = {}
    for script in TESTS:
        name = Path(script).stem
        proc = subprocess.run(
            [sys.executable, str(ROOT / script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        results[name] = "PASS" if proc.returncode == 0 else "FAIL"
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr)

    from engines.work_report import write_work_report

    path = write_work_report(
        ROOT,
        task_title="Language Intelligence v2 — Self Learning",
        discovered=[
            "v1 lacked Naturalness tiers, 4-question gate, semantic validator depth.",
            "Need context rules (George Jr vs Junior Engineer).",
            "Need Analysis Only mode and Language_Report.txt.",
        ],
        root_cause="Quality layer must never guess — confidence + semantic validation required.",
        changes=[
            "v2: naturalness.py, confidence.py, context.py, semantic_validator.py, style_analyzer.py.",
            "Four questions before any fix; Naturalness Score tiers 0-100.",
            "Analysis Only: VM_LANGUAGE_INTELLIGENCE_ANALYSIS=1.",
            "integration.py — one-line hook apply_before_tts (optional).",
            "Language_Report.txt with applied/rejected/suggestions/avg naturalness.",
            "Performance fast mode when segment budget exceeded.",
            "context_rules.json for context-aware fixes.",
            "NO changes to Marian, Naturalizer, TTS, pipeline files.",
        ],
        files_changed=[
            "engines/language_intelligence/ (v2 modules)",
            "data/language_intelligence/context_rules.json",
            "scripts/test_language_intelligence_v2.py",
        ],
        functions_changed=[
            "process_segments v2",
            "four_questions_ok",
            "validate_semantic_preserve",
            "apply_before_tts",
            "write_language_report",
        ],
        tests_run=list(results.keys()),
        test_results=results,
        remaining_checks=[
            "Optional: add one integration line in pipeline when approved.",
            "Manual EN→UK with VM_LANGUAGE_INTELLIGENCE=1.",
        ],
        limitations=[
            "Not wired into auto_dub by default — zero impact on current TubeDub.",
            "Windows spell API optional stub only.",
            "Semantic validation heuristic, not LLM.",
        ],
        next_actions=[
            "Enable VM_LANGUAGE_INTELLIGENCE=1 for testing.",
            "Use integration.apply_before_tts() one-liner when ready.",
        ],
        fixed=["LI v2 architecture", "Self-learning gates", "Reports"],
        not_fixed=["Pipeline auto-wire (intentional)"],
        status="READY" if all(v == "PASS" for v in results.values()) else "WARNING",
    )
    print(f"WORK_REPORT: {path}")
    return 0 if all(v == "PASS" for v in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
