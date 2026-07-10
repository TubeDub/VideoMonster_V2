"""Auto work report after dub — Naturalizer / Quality Score phase."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _avg_quality(audits: list[dict[str, Any]]) -> float:
    scores = [float(a.get("quality_score") or 0) for a in audits if a.get("quality_score")]
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def _naturalizer_changed(audits: list[dict[str, Any]]) -> int:
    n = 0
    for a in audits:
        raw = str(a.get("raw_translation") or "").strip()
        nat = str(a.get("naturalized_text") or "").strip()
        if raw and nat and raw != nat:
            n += 1
    return n


def write_post_dub_work_report(
    app_dir: Path,
    *,
    task_id: str = "",
    info: dict[str, Any] | None = None,
    success: bool = True,
) -> str:
    from engines.work_report import write_work_report

    info = info or {}
    audits = info.get("translation_audits") or []
    tests_run: list[str] = []
    test_results: dict[str, str] = {}

    for script in (
        "scripts/test_quality_score_fix.py",
        "scripts/test_proper_nouns_dict.py",
        "scripts/test_naturalizer_unit.py",
        "scripts/test_stable_translate.py",
    ):
        name = Path(script).stem
        tests_run.append(name)
        proc = subprocess.run(
            [sys.executable, str(app_dir / script)],
            cwd=str(app_dir),
            capture_output=True,
            text=True,
        )
        test_results[name] = "PASS" if proc.returncode == 0 else "FAIL"

    all_pass = all(v == "PASS" for v in test_results.values())
    avg_q = _avg_quality(audits)
    changed = _naturalizer_changed(audits)
    alt_count = sum(1 for a in audits if a.get("alternative_translation"))

    try:
        if os.getenv("VM_LANGUAGE_INTELLIGENCE", "").strip().lower() in ("1", "true", "yes"):
            from engines.language_intelligence.report import write_language_report

            write_language_report(
                app_dir,
                task_id=task_id,
                extra={"phase": "post_dub", "avg_quality": avg_q},
            )
    except Exception:
        pass

    path = write_work_report(
        app_dir,
        task_title=f"Post-dub quality report task={task_id}",
        discovered=[
            "Naturalizer output often matched Raw MT (rules too narrow; calque fix only in router path).",
            "Stable Marian hardcoded quality_score=100 regardless of output quality.",
            "No proper-nouns dictionary for brands like Fiat, USC, Star Wars.",
        ],
        root_cause=(
            "Naturalizer applied minimal regex fixes; semantic calque polish skipped in stable mode; "
            "QE score not computed after Marian in stable path."
        ),
        changes=[
            "Expanded Ukrainian ruism/calque fixes in translation_naturalizer.py.",
            "apply_style_polish: calques + proper-nouns restore before keep_if_not_worse.",
            "Real compute_quality_score after stable Marian (metadata only — Marian unchanged).",
            "Quality score: uk ruism, brand mistranslation, semantic calque penalties; removed double penalty.",
            "data/proper_nouns_never_translate.json + engines/proper_nouns_dict.py.",
            "LLM polish prompt: style-only, no full rewrite.",
        ],
        files_changed=[
            "engines/translation_naturalizer.py",
            "engines/translation_quality_score.py",
            "engines/translation_quality.py",
            "engines/mt/stable_translate.py",
            "engines/proper_nouns_dict.py",
            "data/proper_nouns_never_translate.json",
            "engines/dub_quality_report.py",
            "api/auto_dub_api.py",
            "scripts/test_quality_score_fix.py",
            "scripts/test_proper_nouns_dict.py",
        ],
        functions_changed=[
            "apply_style_polish",
            "polish_lines",
            "compute_quality_score",
            "translate_direct_marian (quality metadata)",
            "restore_never_translate_tokens",
            "write_post_dub_work_report",
        ],
        tests_run=tests_run,
        test_results=test_results,
        remaining_checks=[
            "Manual EN→UK dub: verify Naturalized differs from Raw where ruism/calque present.",
            "Manual: Fiat/USC/Star Wars stay Latin in output.",
            "Extend proper_nouns_never_translate.json for project-specific names.",
            "Optional: LLM polish when OPENAI_API_KEY set (style-only).",
        ],
        limitations=[
            "Marian MT and stable routing unchanged.",
            "Rule-based naturalizer cannot fix all awkward grammar — LLM optional.",
            "Proper-nouns restore requires dictionary entry or cyrillic_mistranslations map.",
            f"This dub: segments={len(audits)} naturalizer_changed={changed} avg_quality={avg_q} alt_routes={alt_count}.",
        ],
        next_actions=[
            "Review translation editor warnings and quality scores per segment.",
            "Add domain-specific brands to proper_nouns_never_translate.json.",
        ],
        fixed=[
            "Real quality score after Marian (stable path)",
            "Stronger Naturalizer for Ukrainian",
            "Proper nouns never-translate dictionary",
            "Auto WORK_REPORT after dub",
        ],
        not_fixed=[
            "Marian/Router/pipeline architecture",
            "Full literary rewrite without LLM",
        ],
        status="READY" if success and all_pass else ("WARNING" if success else "ERROR"),
    )
    return path
