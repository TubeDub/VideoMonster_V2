"""Commercial phase report — UX/TTS architecture."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TESTS = [
    "scripts/test_text_preparation.py",
    "scripts/test_tts_text_path.py",
    "scripts/test_stable_translate.py",
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

    all_pass = all(v == "PASS" for v in results.values())

    from engines.work_report import write_work_report

    path = write_work_report(
        ROOT,
        task_title="Commercial UX phase — TTS architecture, Text Prep, UI",
        discovered=[
            "Single Edge-TTS backend; no pluggable engine layer before this phase.",
            "Translation review showed Raw/Naturalized to all users.",
            "Progress polled every 1.5s without segment/ETA detail.",
            "Technical 60s timeout shown to end users.",
        ],
        root_cause="Product maturity gaps in TTS extensibility and user-facing UX, not translator quality.",
        changes=[
            "Added engines/tts_engines registry (edge-offline + stubs for OpenAI/ElevenLabs/Azure/Google/Studio).",
            "Added engines/text_preparation.py stage before TTS (pipeline unchanged for Marian/Naturalizer).",
            "Simplified translation review: Final text only in user mode; dev mode shows Raw/Route.",
            "Language picker with search; voice list with description + preview button.",
            "Live progress: segment index, ETA estimate, 1s polling.",
            "User-facing long_processing message instead of 60s timeout text.",
            "Unified dub.css design tokens on dub page.",
        ],
        files_changed=[
            "engines/tts_engines/",
            "engines/text_preparation.py",
            "data/tts_engines.json",
            "data/voice_catalog.json",
            "static/css/dub.css",
            "templates/dub.html",
            "static/js/dub.js",
            "api/auto_dub_api.py",
            "scripts/test_text_preparation.py",
        ],
        functions_changed=[
            "prepare_segments_for_tts",
            "list_engine_infos / synthesize (tts_engines.registry)",
            "renderTranslationReview (user vs dev)",
            "updateProgressLive",
            "api_voice_catalog / api_tts_engines",
            "_update_progress_detail",
        ],
        tests_run=list(results.keys()),
        test_results=results,
        remaining_checks=[
            "Manual: dub EN→UK — voice preview, review editor, progress live text.",
            "Manual: verify Marian/Naturalizer output unchanged (not modified in this phase).",
            "Implement real OpenAI/ElevenLabs/Azure/Google TTS when API keys provided.",
            "Extend voice_catalog.json for all languages.",
            "Apply dub.css patterns to settings/studio pages.",
        ],
        limitations=[
            "Online TTS engines are registry stubs only (require API keys + implementation).",
            "Edge-TTS does not support manual stress marks; stress hook reserved for future engines.",
            "ETA is heuristic within current pipeline step, not wall-clock for full job.",
            "E2E MP4 test not run in CI.",
        ],
        next_actions=[
            "User manual test short video EN→UK with translation review enabled.",
            "Prioritize ElevenLabs or Azure integration if natural intonation required.",
            "Roll unified design system to settings and Download Center.",
        ],
        fixed=[
            "TTS engine registry architecture",
            "Text Preparation stage before TTS",
            "User-only Final editor",
            "Live progress UI",
            "User-friendly long wait message",
        ],
        not_fixed=[
            "Full online TTS provider implementations",
            "Cross-page design unification outside /dub",
            "Automated E2E MP4 regression in CI",
        ],
        status="READY" if all_pass else "WARNING",
    )
    print(f"WORK_REPORT: {path}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
