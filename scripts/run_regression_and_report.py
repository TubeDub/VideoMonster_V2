"""Run regression tests and write WORK_REPORT."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TESTS = [
    "scripts/test_stable_translate.py",
    "scripts/test_simple_router.py",
    "scripts/test_translation_router.py",
    "scripts/test_route_planner.py",
    "scripts/test_prepare_pairs.py",
    "scripts/test_model_manager.py",
    "scripts/test_model_cache.py",
]


def main() -> int:
    results: dict[str, str] = {}
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
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)

    all_pass = all(v == "PASS" for v in results.values())
    status = "READY" if all_pass else "ERROR"

    from engines.work_report import write_work_report

    path = write_work_report(
        ROOT,
        task_title="Fix translation hang at 55% — stable Marian path",
        discovered=[
            "Dub hung at translate step (55%) after Router simplification + 60s timeout.",
            "run_with_timeout() wrapped PyTorch Marian inference in ThreadPoolExecutor worker thread.",
            "On Windows, PyTorch model.generate() in a non-main thread blocks or runs indefinitely.",
            "Timeout only cancelled the wait; worker thread kept running (zombie inference).",
        ],
        root_cause=(
            "ThreadPoolExecutor in translate_guard.run_with_timeout() ran Marian/PyTorch "
            "in a background thread. This is incompatible with stable PyTorch inference on Windows "
            "and caused the translate phase to stall until the 60s timeout fired."
        ),
        changes=[
            "Default production path: direct Marian in main thread (stable_translate.py).",
            "Router/Pivot/cascade disabled unless VM_USE_ROUTER=1 and VM_DEV_MODE=1.",
            "Removed ThreadPoolExecutor from translate_with_best_engine (registry.py).",
            "Sequential translation only in stable mode; Translation Memory disabled in stable mode.",
            "START/END translation stage logging (translation_stage_log.py).",
            "Marian num_beams=1 in stable mode for faster CPU inference.",
            "OfflineOnlyError surfaced if model not prepared before dub.",
        ],
        files_changed=[
            "engines/mt/stable_translate.py (new)",
            "engines/translation_stage_log.py (new)",
            "engines/work_report.py (new)",
            "engines/translation.py",
            "engines/translation_pipeline.py",
            "engines/mt/registry.py",
            "engines/mt/marian_engine.py",
            "api/auto_dub_api.py",
            "scripts/test_stable_translate.py (new)",
            "scripts/run_regression_and_report.py (new)",
        ],
        functions_changed=[
            "translate_text_traced → stable Marian by default",
            "translate_with_best_engine → main-thread only",
            "UniversalTranslationPipeline.translate_segments → START/END logs, stable_v1 pipeline",
            "translate_direct_marian, ensure_marian_ready (new)",
            "log_start, log_end (new)",
        ],
        tests_run=list(results.keys()),
        test_results=results,
        remaining_checks=[
            "Manual E2E dub: EN→RU, EN→UK, RU→UK, UK→RU — confirm no hang at 55%, MP4 output.",
            "Verify output/dev/translation_stage_latest.log shows START and END TRANSLATION.",
            "Confirm no HuggingFace download during dub (offline lock).",
        ],
        limitations=[
            "Per-segment interrupt mid-generate not possible without process kill on Windows.",
            "Semantic adaptation skipped in stable_v1 pipeline (Naturalizer only).",
            "Router path still available for dev via VM_USE_ROUTER=1 + VM_DEV_MODE=1.",
        ],
        next_actions=[
            "Run full dub on EN→UK with prepared Marian model.",
            "If stable, re-enable Semantic Adaptation one step at a time.",
            "Re-enable Router only after E2E passes on all four pairs.",
        ],
        fixed=[
            "Root cause identified: PyTorch in ThreadPoolExecutor worker thread.",
            "Production dub uses main-thread Marian only.",
            "START/END translation diagnostics added.",
        ],
        not_fixed=[
            "Full E2E dub on real video not executed in CI (requires models + FFmpeg + video).",
        ],
        status=status,
    )
    print(f"WORK_REPORT: {path}")
    print(f"STATUS: {status}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
