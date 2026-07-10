"""Post-run validation checks — read-only analysis of pipeline output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


STAGE_KEYS = (
    "whisper",
    "language",
    "router",
    "mt",
    "naturalizer",
    "language_intelligence",
    "semantic_adaptation",
    "tts",
    "timing",
    "mux",
    "mp4",
)


def _latin_words(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z]{2,}\b", str(text or ""))


def analyze_task_result(
    *,
    task: dict[str, Any],
    info: dict[str, Any],
    review: dict[str, Any],
    output_path: Path | None,
    log_paths: list[str],
    app_dir: Path,
) -> dict[str, Any]:
    """Run automatic checks after one dub job."""
    issues: list[dict[str, Any]] = []
    stages: dict[str, str] = {k: "unknown" for k in STAGE_KEYS}
    tgt = str(info.get("target_lang") or "uk").split("-")[0].lower()
    src = str(info.get("detected_lang") or info.get("source_lang") or "en").split("-")[0].lower()

    status = str(task.get("status") or "")
    errors = list(task.get("errors") or [])

    # Stage inference from task metadata
    if info.get("source_segments"):
        stages["whisper"] = "ok"
    if info.get("detected_lang") or info.get("source_lang"):
        stages["language"] = "ok"
    audits = info.get("translation_audits") or []
    if audits:
        stages["router"] = "ok"
        stages["mt"] = "ok"
        if any(a.get("naturalized_text") for a in audits):
            stages["naturalizer"] = "ok"
        else:
            stages["naturalizer"] = "skipped"
        if any(a.get("semantic_adapted") for a in audits):
            stages["semantic_adaptation"] = "ok"
        else:
            stages["semantic_adaptation"] = "none"
    segments_data = info.get("segments_data") or []
    if segments_data:
        stages["tts"] = "ok"
    if info.get("timing_map") or info.get("timing_map_backup"):
        stages["timing"] = "ok"
    if output_path and output_path.is_file() and output_path.stat().st_size > 0:
        stages["mux"] = "ok"
        stages["mp4"] = "ok"
    elif status == "done":
        stages["mux"] = "fail"
        stages["mp4"] = "fail"
        issues.append({"code": "mp4_missing", "severity": "critical"})

    if status == "error":
        issues.append({"code": "pipeline_error", "severity": "critical", "detail": errors[:5]})

    rows = review.get("segments") or []
    quality_scores: list[float] = []

    for row in rows:
        qs = row.get("quality_score")
        if qs is not None:
            quality_scores.append(float(qs))

        final = str(row.get("final_text") or "")
        tts = str(row.get("tts_text") or "")
        original = str(row.get("original") or "")
        raw = str(row.get("raw_translation") or "")
        naturalized = str(row.get("naturalized_text") or "")

        if original.strip() and not (final.strip() or tts.strip()):
            issues.append(
                {
                    "code": "empty_segment",
                    "severity": "critical",
                    "segment": row.get("index"),
                }
            )

        if final.strip() and tts.strip() and final.strip() != tts.strip():
            issues.append(
                {
                    "code": "final_tts_mismatch",
                    "severity": "warning",
                    "segment": row.get("index"),
                    "final": final[:80],
                    "tts": tts[:80],
                }
            )

        for w in row.get("warnings") or []:
            code = str(w.get("code") or "warning")
            issues.append(
                {
                    "code": code,
                    "severity": "warning",
                    "segment": row.get("index"),
                    "stage": w.get("stage"),
                    "detail": w,
                }
            )

        if tgt in ("uk", "ru"):
            latin = _latin_words(final or tts)
            keep = {w.lower() for w in _latin_words(original)}
            leaked = [w for w in latin if w.lower() not in keep]
            if len(leaked) >= 2:
                issues.append(
                    {
                        "code": "english_leak",
                        "severity": "warning",
                        "segment": row.get("index"),
                        "tokens": leaked[:6],
                    }
                )

        if raw.strip() and naturalized.strip() and raw != naturalized:
            from engines.semantic_translation import detect_semantic_issues

            for issue in detect_semantic_issues(
                original, naturalized, source_lang=src, target_lang=tgt
            ):
                if issue.get("code") in ("negation_lost", "numbers_changed", "meaning_drift"):
                    issues.append(
                        {
                            "code": "naturalizer_meaning_drift",
                            "severity": "warning",
                            "segment": row.get("index"),
                            "detail": issue,
                        }
                    )

    source_count = len(info.get("source_segments") or [])
    seg_count = len(segments_data)
    if source_count and seg_count and abs(source_count - seg_count) > max(2, source_count * 0.15):
        issues.append(
            {
                "code": "segment_count_drift",
                "severity": "warning",
                "source": source_count,
                "translated": seg_count,
            }
        )

    timing_map = info.get("timing_map") or info.get("timing_map_backup") or []
    if timing_map and segments_data:
        bad_timing = 0
        for i, tm in enumerate(timing_map[: len(segments_data)]):
            try:
                if isinstance(tm, (list, tuple)) and len(tm) >= 2:
                    start, end = int(tm[0]), int(tm[1])
                    if end <= start:
                        bad_timing += 1
                elif isinstance(tm, str) and "-->" in tm:
                    parts = tm.split("-->")
                    if len(parts) == 2:
                        a, b = parts[0].strip(), parts[1].strip()
                        if a >= b:
                            bad_timing += 1
            except Exception:
                bad_timing += 1
        if bad_timing:
            stages["timing"] = "warn"
            issues.append({"code": "timing_invalid", "severity": "warning", "count": bad_timing})

    for lp in log_paths:
        p = Path(lp)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[-8000:]
        except Exception:
            continue
        rel = str(p)
        try:
            rel = str(p.relative_to(app_dir))
        except ValueError:
            pass
        for pat in ("Traceback", "CRITICAL", "FATAL", "Exception:"):
            if pat in text:
                issues.append(
                    {
                        "code": "log_critical",
                        "severity": "warning",
                        "log": rel,
                        "pattern": pat,
                    }
                )
                break

    li_log = app_dir / "output" / "dev" / "language_intelligence.log"
    if li_log.is_file() and "fixes_applied" in li_log.read_text(encoding="utf-8", errors="replace")[-4000:]:
        stages["language_intelligence"] = "ok"
    else:
        stages["language_intelligence"] = "none"

    passed = status == "done" and stages.get("mp4") == "ok" and not any(
        i.get("severity") == "critical" for i in issues
    )

    return {
        "passed": passed,
        "status": status,
        "stages": stages,
        "issues": issues,
        "quality_scores": quality_scores,
        "avg_quality": round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None,
        "segment_count": seg_count,
        "source_count": source_count,
        "errors": errors,
    }
