"""Language Intelligence v2 pipeline."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from engines.language_intelligence.confidence import decide, may_apply, may_suggest
from engines.language_intelligence.config import is_analysis_only, is_enabled
from engines.language_intelligence.fixer import apply_proposal, propose_all_fixes
from engines.language_intelligence.learner import run_background_learning
from engines.language_intelligence.log_util import log_job_end, log_job_start, log_segment_fix
from engines.language_intelligence.memory import permanent_rules
from engines.language_intelligence.naturalness import (
    aggregate_scores,
    four_questions_ok,
    tier_from_score,
)
from engines.language_intelligence.performance import PerformanceGuard
from engines.language_intelligence.report import write_language_report
from engines.language_intelligence.semantic_validator import validate_semantic_preserve
from engines.language_intelligence.style_analyzer import analyze_style


def _process_one_v2(
    *,
    original: str,
    raw_mt: str,
    naturalized: str,
    final: str,
    src_lang: str,
    tgt_lang: str,
    index: int,
    app_dir: Path,
    learned: list[dict[str, Any]],
    perf: PerformanceGuard,
    analysis_only: bool,
) -> tuple[str, dict[str, Any]]:
    t0 = perf.start_segment()
    text_in = str(final or naturalized or raw_mt or "").strip()

    style = analyze_style(
        original=original,
        raw_mt=raw_mt,
        naturalized=naturalized,
        final=text_in,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
    )
    tier = tier_from_score(style.naturalness_score)

    meta: dict[str, Any] = {
        "naturalness_score": style.naturalness_score,
        "tier": tier.tier,
        "changed": False,
        "fixes_applied": [],
        "fixes_rejected": [],
        "suggestions": [],
        "issues": style.issues,
    }

    if tier.action == "skip" and not style.objective_issue:
        perf.end_segment(t0)
        return text_in, meta

    proposals = propose_all_fixes(
        original=original,
        final=text_in,
        tgt_lang=tgt_lang,
        learned_rules=learned,
        app_dir=app_dir,
        fast_mode=perf.fast_mode,
    )

    current = text_in
    for prop in proposals:
        conf = float(prop.get("confidence") or 0.0)
        conf_dec = decide(conf)
        candidate = apply_proposal(current, prop, original=original)
        sem_ok, sem_fail = validate_semantic_preserve(original, current, candidate)

        ok, block_reasons = four_questions_ok(
            naturalness=tier,
            has_objective_issue=style.objective_issue,
            confidence=conf,
            semantic_ok=sem_ok,
        )

        entry = {
            **prop,
            "confidence": conf,
            "semantic_ok": sem_ok,
            "semantic_failures": sem_fail,
            "block_reasons": block_reasons,
        }

        if analysis_only or may_suggest(conf_dec):
            meta["suggestions"].append({**entry, "would_be": candidate})
            if analysis_only:
                continue

        if not ok or not may_apply(conf_dec, semantic_ok=sem_ok):
            meta["fixes_rejected"].append(entry)
            continue

        if analysis_only:
            continue

        current = candidate
        meta["fixes_applied"].append(entry)
        log_segment_fix(index, entry, app_dir=app_dir)

    meta["changed"] = current != text_in
    perf.end_segment(t0)
    return (current if not analysis_only else text_in), meta


def process_segment(
    *,
    original: str,
    raw_mt: str,
    naturalized: str,
    final: str,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    index: int = 0,
    app_dir: Path | None = None,
    task_id: str = "",
    write_log: bool = True,
) -> tuple[str, dict[str, Any]]:
    if not is_enabled():
        return str(final or naturalized or raw_mt or ""), {"enabled": False, "changed": False}

    base = app_dir or Path(__file__).resolve().parent.parent.parent
    learned = permanent_rules(base)
    perf = PerformanceGuard()
    improved, meta = _process_one_v2(
        original=original,
        raw_mt=raw_mt,
        naturalized=naturalized,
        final=final,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        index=index,
        app_dir=base,
        learned=learned,
        perf=perf,
        analysis_only=is_analysis_only(),
    )
    meta["enabled"] = True
    return improved, meta


def process_segments(
    segments: list[dict[str, Any]],
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    task_id: str = "",
    app_dir: Path | None = None,
    learn_after: bool = True,
    write_report_file: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    base = app_dir or Path(__file__).resolve().parent.parent.parent
    t0 = time.perf_counter()

    if not is_enabled():
        finals = [
            str(s.get("final") or s.get("naturalized") or s.get("raw_mt") or "")
            for s in segments
        ]
        return finals, {"enabled": False, "segments": len(segments), "changed": 0}

    log_job_start(task_id, app_dir=base)
    learned = permanent_rules(base)
    perf = PerformanceGuard()
    analysis_only = is_analysis_only()

    improved_list: list[str] = []
    all_applied: list[dict[str, Any]] = []
    all_rejected: list[dict[str, Any]] = []
    all_suggestions: list[dict[str, Any]] = []
    naturalness_scores: list[float] = []
    changed_count = 0
    error_freq: dict[str, int] = {}

    for i, seg in enumerate(segments):
        original = str(seg.get("original") or seg.get("whisper") or "")
        raw_mt = str(seg.get("raw_mt") or seg.get("raw_translation") or "")
        naturalized = str(seg.get("naturalized") or seg.get("naturalized_text") or "")
        final = str(seg.get("final") or seg.get("final_text") or naturalized or raw_mt)

        improved, meta = _process_one_v2(
            original=original,
            raw_mt=raw_mt,
            naturalized=naturalized,
            final=final,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            index=i + 1,
            app_dir=base,
            learned=learned,
            perf=perf,
            analysis_only=analysis_only,
        )
        improved_list.append(improved)
        naturalness_scores.append(float(meta.get("naturalness_score") or 100))

        if meta.get("changed"):
            changed_count += 1
        for fx in meta.get("fixes_applied") or []:
            all_applied.append({**fx, "segment_index": i + 1})
            code = str(fx.get("code") or "other")
            error_freq[code] = error_freq.get(code, 0) + 1
        for fx in meta.get("fixes_rejected") or []:
            all_rejected.append({**fx, "segment_index": i + 1})
        for fx in meta.get("suggestions") or []:
            all_suggestions.append({**fx, "segment_index": i + 1})

    elapsed = round(time.perf_counter() - t0, 3)
    learn_meta: dict[str, Any] = {}
    if learn_after and all_applied and not analysis_only:
        learn_meta = run_background_learning(
            all_applied,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            app_dir=base,
        )

    job_meta: dict[str, Any] = {
        "enabled": True,
        "version": "2.0",
        "task_id": task_id,
        "segments": len(segments),
        "changed": changed_count,
        "fixes_applied": len(all_applied),
        "fixes_rejected": len(all_rejected),
        "suggestions": len(all_suggestions),
        "avg_naturalness": aggregate_scores(naturalness_scores),
        "naturalness_scores": naturalness_scores,
        "error_frequency": error_freq,
        "elapsed_sec": elapsed,
        "fast_mode": perf.fast_mode,
        "avg_ms_per_segment": round(perf.avg_ms, 2),
        "analysis_only": analysis_only,
        "new_rules": learn_meta.get("promoted_rules") or [],
        "learning": learn_meta,
        "applied_details": all_applied,
        "rejected_details": all_rejected,
        "suggestion_details": all_suggestions,
    }

    log_job_end(job_meta, app_dir=base)
    if write_report_file:
        write_language_report(job_meta, app_dir=base)

    return improved_list, job_meta
