"""Generate WORK_REPORT for Language Intelligence module."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_language_intelligence.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    ok = proc.returncode == 0
    if not ok:
        print(proc.stdout, proc.stderr)

    from engines.work_report import write_work_report

    path = write_work_report(
        ROOT,
        task_title="Language Intelligence — autonomous post-translation module",
        discovered=[
            "Need isolated layer between Naturalizer and TTS without touching Marian/pipeline.",
            "Self-learning must not promote rules after single occurrence.",
        ],
        root_cause="Quality improvements must be optional, removable, and pipeline-safe.",
        changes=[
            "Created engines/language_intelligence/ (analyzer, fixer, learner, memory, rules, validator, report, pipeline).",
            "Single API: process_segment / process_segments → Improved Final.",
            "Default OFF: VM_LANGUAGE_INTELLIGENCE=0.",
            "Memory: data/language_intelligence/learning_rules.json + language_memory.db.",
            "Reports: output/reports/LANGUAGE_INTELLIGENCE_REPORT.txt.",
            "Log: output/dev/language_intelligence.log.",
            "NO changes to Whisper, Marian, Naturalizer, TTS, Timing, Mux, Router, Stable Pipeline.",
        ],
        files_changed=[
            "engines/language_intelligence/__init__.py",
            "engines/language_intelligence/analyzer.py",
            "engines/language_intelligence/fixer.py",
            "engines/language_intelligence/learner.py",
            "engines/language_intelligence/memory.py",
            "engines/language_intelligence/rules.py",
            "engines/language_intelligence/validator.py",
            "engines/language_intelligence/report.py",
            "engines/language_intelligence/pipeline.py",
            "engines/language_intelligence/log_util.py",
            "data/language_intelligence/learning_rules.json",
            "scripts/test_language_intelligence.py",
        ],
        functions_changed=["process_segments", "process_segment", "is_enabled", "ingest_job_corrections"],
        tests_run=["test_language_intelligence"],
        test_results={"test_language_intelligence": "PASS" if ok else "FAIL"},
        remaining_checks=[
            "Optional integration hook (3 lines) in pipeline when VM_LANGUAGE_INTELLIGENCE=1 — not wired yet by design.",
            "Settings UI checkbox — future, use env var for now.",
            "Manual EN→UK dub with VM_LANGUAGE_INTELLIGENCE=1 via test script or future hook.",
        ],
        limitations=[
            "Module not wired into auto_dub pipeline — zero impact on current TubeDub.",
            "Deleting engines/language_intelligence/ leaves app unchanged.",
            "Learned rules need ≥5 repeats and ≥85% success before promotion.",
            "Meaning guard is heuristic, not LLM-based.",
        ],
        next_actions=[
            "When ready: optional try/import hook after Naturalizer (user approval).",
            "Set VM_LANGUAGE_INTELLIGENCE=1 to test via scripts/test_language_intelligence.py.",
        ],
        fixed=["Autonomous Language Intelligence module"],
        not_fixed=["Pipeline integration (intentionally deferred)", "Settings UI toggle"],
        status="READY" if ok else "WARNING",
    )
    print(f"WORK_REPORT: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
