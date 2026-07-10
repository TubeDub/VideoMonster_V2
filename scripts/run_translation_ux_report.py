"""Generate WORK_REPORT for Translation + UX phase."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TESTS = [
    "scripts/test_naturalizer_phase.py",
    "scripts/test_naturalizer_unit.py",
    "scripts/test_quality_score_fix.py",
    "scripts/test_proper_nouns_dict.py",
    "scripts/test_stable_translate.py",
    "scripts/test_tts_text_path.py",
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

    app_ok = False
    try:
        import app  # noqa: F401

        app_ok = True
    except Exception as exc:
        print("import app FAIL:", exc)

    all_pass = all(v == "PASS" for v in results.values()) and app_ok

    from engines.work_report import write_work_report

    path = write_work_report(
        ROOT,
        task_title="Translation + UX phase — Naturalizer, Quality Score, UI polish",
        discovered=[
            "Raw MT = Naturalized = Final in most segments (Naturalizer skipped on low score).",
            "Quality Score always 100 on stable Marian path.",
            "Language list always expanded — poor ComboBox UX.",
            "No immediate feedback after Dub button click.",
        ],
        root_cause=(
            "polish_lines skipped segments when score<55; keep_if_not_worse too strict; "
            "semantic calque pass disabled in stable mode; hardcoded quality_score=100."
        ),
        changes=[
            "Naturalizer always runs; score<70 triggers aggressive polish + optional LLM.",
            "accept_naturalizer_change — permissive style rollback.",
            "Stable pipeline: apply_semantic_polish_lines for Final != Naturalized.",
            "Proper nouns v2: keep_latin, preferred_translations, transliterate_names.",
            "Quality Score: ruism, calque, brand/title penalties; real score after Marian.",
            "UI: collapsed language ComboBox, launch dots, ui_sounds.js, CSS transitions.",
            "Settings: uiSounds toggle.",
            "Auto WORK_REPORT after each dub (dub_quality_report).",
        ],
        files_changed=[
            "engines/translation_naturalizer.py",
            "engines/translation_pipeline.py",
            "engines/translation_quality.py",
            "engines/translation_quality_score.py",
            "engines/proper_nouns_dict.py",
            "data/proper_nouns_never_translate.json",
            "templates/dub.html",
            "static/js/dub.js",
            "static/js/ui_sounds.js",
            "static/css/dub.css",
            "templates/settings.html",
            "scripts/test_naturalizer_phase.py",
            "scripts/run_translation_ux_report.py",
        ],
        functions_changed=[
            "polish_lines",
            "apply_style_polish / apply_proper_noun_polish",
            "accept_naturalizer_change",
            "needs_aggressive_natural",
            "initLanguagePicker (ComboBox)",
            "showDubStarting",
            "write_post_dub_work_report",
        ],
        tests_run=list(results.keys()) + ["import_app"],
        test_results={**results, "import_app": "PASS" if app_ok else "FAIL"},
        remaining_checks=[
            "Manual EN→UK dub: verify Naturalized != Raw where ruism present.",
            "Manual: ComboBox language picker closes after selection.",
            "Manual: dub start shows green dots immediately.",
            "Manual: disable UI sounds in Settings.",
        ],
        limitations=[
            "Marian/Whisper/TTS/Timing/Mux/stable pipeline unchanged.",
            "Star Wars → Зоряні війни via dictionary; extend JSON for more titles.",
            "LLM polish optional (OPENAI_API_KEY).",
            "Final==Naturalized when no calque rules match.",
        ],
        next_actions=[
            "Run short EN→UK dub and check translation review stages in dev mode.",
            "Add project-specific proper nouns to JSON.",
        ],
        fixed=[
            "Naturalizer always active",
            "Real Quality Score",
            "Proper nouns policy v2",
            "Language ComboBox UX",
            "Launch indicator + UI sounds",
            "WORK_REPORT after dub",
        ],
        not_fixed=[
            "Full literary rewrite without LLM",
            "Revolver/wheel language picker (deferred)",
            "Cross-page design outside /dub",
        ],
        status="READY" if all_pass else "WARNING",
    )
    print(f"WORK_REPORT: {path}")
    print(f"App startup: {'OK' if app_ok else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
