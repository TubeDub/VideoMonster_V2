"""Build user-facing translation review data (language-agnostic)."""

from __future__ import annotations

from typing import Any

from engines.translation_quality import (
    extract_preserved_tokens,
    missing_preserved_tokens,
    segment_quality_warnings,
)
from engines.translation_stage_log import text_fingerprint

__all__ = [
    "build_translation_review",
    "export_review_text",
    "extract_proper_nouns",
    "format_warning_for_export",
    "proper_noun_warnings",
]

extract_proper_nouns = extract_preserved_tokens
proper_noun_warnings = missing_preserved_tokens


def _is_ssml(text: str) -> bool:
    return str(text or "").lstrip().startswith("<speak")


def _dedupe_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for w in warnings:
        tokens = tuple(sorted(w.get("tokens") or w.get("names") or []))
        key = (w.get("code"), w.get("stage"), tokens)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


def _resolve_final_text(seg: dict, audit: dict) -> str:
    final = str(audit.get("final_text") or "").strip()
    if final and not _is_ssml(final):
        return final
    for key in ("plain_text", "translation_text"):
        val = str(seg.get(key) or "").strip()
        if val and not _is_ssml(val):
            return val
    val = str(seg.get("text") or "").strip()
    if val and not _is_ssml(val):
        return val
    nat = str(audit.get("naturalized_text") or "").strip()
    return nat if nat and not _is_ssml(nat) else ""


def _resolve_text_for_tts(seg: dict, audit: dict, *, final: str, tts_synthesized: bool) -> str:
    """UI/TTS bound text — before synthesis equals final; after synthesis equals spoken text."""
    if not tts_synthesized:
        return final
    for key in ("tts_text",):
        val = str(audit.get(key) or seg.get(key) or "").strip()
        if val and not _is_ssml(val):
            return val
    ssml = str(audit.get("tts_text") or seg.get("text") or "").strip()
    if ssml.lstrip().startswith("<speak"):
        return ssml
    return final


def _resolve_review_warnings(
    audit: dict,
    *,
    original: str,
    raw: str,
    naturalized: str,
    final: str,
    tts_text: str,
    source_lang: str | None,
    target_lang: str | None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """
    Return warnings, qa_invoked, qa_recommendations_applied.
    QA is advisory-only — recommendations are never auto-applied to text.
    """
    fp = text_fingerprint(raw, naturalized, final, tts_text)
    stored = list(audit.get("validation_warnings") or [])
    stored_fp = str(audit.get("validation_warnings_fingerprint") or "")

    if stored and stored_fp == fp and not audit.get("user_edited"):
        return _dedupe_warnings(stored), True, False

    warnings = segment_quality_warnings(
        original=original,
        raw=raw,
        naturalized=naturalized,
        final=final,
        tts_text=tts_text,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    warnings = _dedupe_warnings(warnings)
    audit["validation_warnings"] = warnings
    audit["validation_warnings_fingerprint"] = fp
    return warnings, True, False


def build_llm_status(task_info: dict[str, Any]) -> dict[str, Any]:
    """Top-level advisory: why AI adaptation ran / degraded (model, circuit, entities).

    Answers the user's «почему качество низкое» directly: which model was used,
    whether it was adequate, how many segments never got LLM adaptation, and
    which named entities are at risk — with a concrete recommendation.
    """
    caps = dict(task_info.get("adaptation_capabilities") or {})
    diag = dict(task_info.get("llm_diagnostics") or {})
    eff = dict(task_info.get("llm_effectiveness") or {})

    model = str(caps.get("llm_model") or (diag.get("models") or [""])[0] or "")
    provider = str(caps.get("provider") or (diag.get("providers") or [""])[0] or "")
    adequate = caps.get("llm_model_adequate")
    if adequate is None:
        adequate = caps.get("model_adequate")
    warning = str(caps.get("llm_model_warning") or caps.get("model_warning") or "")

    skip_reasons = dict(diag.get("skip_reasons") or {})
    segments_without_adaptation = int(eff.get("llm_not_called_segments") or 0)
    circuit_tripped = bool(
        skip_reasons.get("llm_circuit_open") or skip_reasons.get("segment_breaker_open")
    )
    model_too_slow = bool(
        diag.get("avg_call_ms") and float(diag.get("avg_call_ms") or 0) >= 30000
    )

    # Aggregate entity risk from per-segment preserved_token warnings.
    # Warnings may live on segments_data rows and/or the translation_audits.
    entity_risk: list[dict[str, Any]] = []
    seen_idx: set[int] = set()

    def _collect(idx: int, warnings: Any) -> None:
        for w in warnings or []:
            if not isinstance(w, dict):
                continue
            if w.get("code") in ("preserved_token", "proper_noun", "meaning_preserved_token"):
                names = w.get("tokens") or w.get("names") or w.get("hints") or []
                if isinstance(names, str):
                    names = [names]
                clean = [n for n in names if n and "REDACTED" not in str(n)]
                if clean and idx not in seen_idx:
                    seen_idx.add(idx)
                    entity_risk.append({"index": idx + 1, "names": clean})

    for i, seg in enumerate(task_info.get("segments_data") or []):
        _collect(i, seg.get("validation_warnings"))
    for audit in task_info.get("translation_audits") or []:
        try:
            idx = int(audit.get("index", -1))
        except (TypeError, ValueError):
            idx = -1
        if idx >= 0:
            _collect(idx, audit.get("validation_warnings"))

    degraded = bool(
        (adequate is False)
        or circuit_tripped
        or model_too_slow
        or segments_without_adaptation > 0
    )

    recommendation = ""
    recommend_cloud = False
    if degraded:
        if adequate is False or model_too_slow:
            recommend_cloud = True
            recommendation = (
                "Локальная модель «%s» слишком мала/медленна для качественной "
                "украинской адаптации. Рекомендуется облачная модель "
                "(задайте OPENAI_API_KEY) или локальная модель ≥7B "
                "(qwen2.5:14b, gemma2:9b, llama3.1:8b) либо GPU." % (model or "—")
            )
        elif circuit_tripped:
            recommendation = (
                "AI-адаптация была отключена в середине прогона из-за таймаутов LLM "
                "(%d сегм. без адаптации). Часть имён и деталей могла потеряться. "
                "Используйте более быструю модель или облако для повторного прогона. "
                "Без LLM остаётся сырой Marian + правила — качество en→uk заметно ниже."
                % segments_without_adaptation
            )
        elif segments_without_adaptation > 0:
            recommend_cloud = True
            recommendation = (
                "LLM-адаптация не вызывалась для %d сегментов. "
                "Проверьте, что Ollama запущена и модель отвечает быстрее таймаута, "
                "либо задайте облачный ключ (OPENAI_API_KEY)."
                % segments_without_adaptation
            )

    return {
        "model": model,
        "provider": provider,
        "model_adequate": bool(adequate) if adequate is not None else None,
        "model_warning": warning,
        "degraded": degraded,
        "circuit_tripped": circuit_tripped,
        "model_too_slow": model_too_slow,
        "avg_call_ms": float(diag.get("avg_call_ms") or 0),
        "llm_calls_total": int(diag.get("call_count") or eff.get("llm_calls_total") or 0),
        "segments_without_adaptation": segments_without_adaptation,
        "entity_risk_segments": entity_risk,
        "entity_risk_count": len(entity_risk),
        "recommend_cloud": recommend_cloud,
        "recommendation": recommendation,
    }


def build_translation_review(task_info: dict[str, Any]) -> dict[str, Any]:
    """Merge Whisper → raw MT → naturalized → final → TTS for UI."""
    source_segments = task_info.get("source_segments") or []
    segments_data = task_info.get("segments_data") or []
    audits = task_info.get("translation_audits") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}
    src_lang = task_info.get("detected_lang") or task_info.get("source_lang")
    tgt_lang = task_info.get("target_lang")
    tts_synthesized = bool(task_info.get("tts_files"))

    rows: list[dict[str, Any]] = []
    warning_count = 0
    qa_invoked_any = False
    for i, src in enumerate(source_segments):
        audit = audit_by_idx.get(i, {})
        seg = segments_data[i] if i < len(segments_data) else {}
        raw = str(audit.get("raw_translation") or "")
        final = _resolve_final_text(seg, audit)
        naturalized = str(audit.get("naturalized_text") or final or "")
        if _is_ssml(naturalized):
            naturalized = final
        # Prefer post-semantic text when naturalizer left Marian unchanged —
        # Review "Naturalized" should not look identical to Raw MT if later
        # polish already improved the line.
        semantic = str(audit.get("semantic_engine_text") or "").strip()
        if (
            semantic
            and not _is_ssml(semantic)
            and naturalized.strip() == raw.strip()
            and semantic != raw.strip()
        ):
            naturalized = semantic
        optimized = str(audit.get("sso_optimized") or "").strip()
        if optimized and _is_ssml(optimized):
            optimized = ""
        text_for_tts = _resolve_text_for_tts(
            seg, audit, final=final, tts_synthesized=tts_synthesized
        )
        original = str(src or audit.get("whisper_text") or "")

        warnings, qa_invoked, qa_applied = _resolve_review_warnings(
            audit,
            original=original,
            raw=raw,
            naturalized=naturalized,
            final=final,
            tts_text=text_for_tts,
            source_lang=src_lang,
            target_lang=tgt_lang,
        )
        qd = audit.get("quality_details") or {}
        quality_analysis = qd.get("quality_analysis") or {}
        qa_invoked_any = qa_invoked_any or qa_invoked
        warning_count += len(warnings)

        if not qd and raw.strip():
            try:
                from engines.quality_score_v2 import compute_quality_score_v2

                computed_score, computed_qd = compute_quality_score_v2(
                    original, raw, src_lang=src_lang, tgt_lang=tgt_lang,
                    naturalized=naturalized,
                )
            except Exception:
                from engines.translation_quality_score import compute_quality_score

                computed_score, computed_qd = compute_quality_score(
                    original, raw, src_lang=src_lang, tgt_lang=tgt_lang
                )
            qd = {**computed_qd, **qd}
            if not audit.get("quality_score"):
                audit["quality_score"] = computed_score

        quality_score = float(audit.get("quality_score") or qd.get("quality_score") or 0)

        rows.append(
            {
                "index": i + 1,
                "original": original,
                "raw_translation": raw,
                "naturalized_text": naturalized,
                "timing_aware_text": str(
                    (audit.get("quality_details") or {}).get("timing_aware", {}).get(
                        "text_after", ""
                    )
                    or final
                ),
                "final_text": final,
                "optimized_text": optimized,
                "text_for_tts": text_for_tts,
                "tts_text": text_for_tts,
                "ui_matches_tts": final == text_for_tts or not tts_synthesized,
                "playback_duration_ms": float(
                    seg.get("playback_duration") or seg.get("tts_ms") or 0
                ),
                "slot_ms": float(seg.get("slot_ms") or audit.get("duration_ms") or 0),
                "post_tts_retries": int(
                    (seg.get("post_tts_retry") or {}).get("attempts") or 0
                ),
                "semantic_adapted": bool(audit.get("semantic_adapted")),
                "naturalizer_applied": bool(
                    audit.get("naturalizer_applied")
                    if audit.get("naturalizer_applied") is not None
                    else (
                        audit.get("naturalizer_reasons")
                        or (
                            naturalized
                            and raw
                            and naturalized.strip() != raw.strip()
                        )
                    )
                ),
                "naturalizer_executed": bool(audit.get("naturalizer_executed", True)),
                "timing_aware_applied": bool(audit.get("timing_aware_applied")),
                "timing_aware_executed": bool(audit.get("timing_aware_executed")),
                "naturalizer_reasons": list(audit.get("naturalizer_reasons") or []),
                "qa_invoked": qa_invoked,
                "qa_recommendations_applied": qa_applied,
                "sso_level": audit.get("sso_level"),
                "prosody": seg.get("prosody") or audit.get("prosody"),
                "engine": str(audit.get("engine") or ""),
                "route": str(audit.get("route") or audit.get("route_label") or "direct"),
                "route_label": str(audit.get("route_label") or ""),
                "router_reason": str(audit.get("router_reason") or ""),
                "pivot": audit.get("pivot"),
                "quality_score": quality_score,
                "alternative_translation": str(audit.get("alternative_translation") or ""),
                "alternative_route": str(audit.get("alternative_route") or ""),
                "alternative_engine": str(audit.get("alternative_engine") or ""),
                "alternative_score": float(audit.get("alternative_score") or 0),
                "routes_tried": list(audit.get("routes_tried") or []),
                "mt_retries": int(audit.get("mt_retries") or 0),
                "duration_ms": float(audit.get("duration_ms") or 0),
                "english_word_count": int(qd.get("english_word_count") or 0),
                "english_word_pct": float(qd.get("english_word_pct") or 0),
                "mixed_language_pct": float(qd.get("mixed_language_pct") or 0),
                "translated_pct": float(qd.get("translated_pct") or 0),
                "warning_count": len(warnings),
                "quality_analysis": quality_analysis,
                "transformation_chain": qd.get("transformation_chain"),
                "user_edited": bool(audit.get("user_edited")),
                "warnings": warnings,
            }
        )

    return {
        "segment_count": len(rows),
        "source_lang": src_lang,
        "target_lang": tgt_lang,
        "warning_count": warning_count,
        "llm_status": build_llm_status(task_info),
        "qa_mode": "advisory",
        "qa_invoked": qa_invoked_any,
        "qa_recommendations_applied": False,
        "qa_note": (
            "Контроль перевода показывает рекомендации и не изменяет текст автоматически. "
            "Озвучивается финальный текст из поля «Финальный текст»."
        ),
        "trace_log": task_info.get("translation_trace_log"),
        "final_dub_qa": task_info.get("final_dub_qa"),
        "post_tts_qa": task_info.get("post_tts_qa"),
        "segments": rows,
    }


def format_warning_for_export(w: dict[str, Any] | str) -> str:
    if isinstance(w, str):
        return w
    code = w.get("code", "")
    stage = w.get("stage", "")
    prefix = f"{stage}:" if stage else ""
    if code == "preserved_token":
        names = ", ".join(w.get("tokens") or w.get("names") or [])
        return f"{prefix}preserved_token: {names}"
    if code == "over_shortening" and w.get("summary"):
        return f"{prefix}{w.get('summary')}"
    if w.get("summary"):
        return f"{prefix}{code}: {w.get('summary')}"
    if code == "proper_noun":
        names = ", ".join(w.get("names") or [])
        return f"{prefix}proper_noun: {names}"
    return f"{prefix}{code}" if prefix else code


def export_review_text(review: dict[str, Any]) -> str:
    lines = [
        "TubeDub — Translation Review",
        f"Source: {review.get('source_lang')} → Target: {review.get('target_lang')}",
    ]
    status = review.get("llm_status") or {}
    if status.get("degraded"):
        lines.append("")
        lines.append("⚠ AI-адаптация деградирована:")
        lines.append(
            f"   Модель: {status.get('model') or '—'} "
            f"(adequate={status.get('model_adequate')}, "
            f"avg_call={round(status.get('avg_call_ms', 0) / 1000, 1)}s)"
        )
        if status.get("segments_without_adaptation"):
            lines.append(
                f"   Сегментов без LLM-адаптации: {status.get('segments_without_adaptation')}"
            )
        if status.get("entity_risk_count"):
            lines.append(
                f"   Сегментов с риском потери имён: {status.get('entity_risk_count')}"
            )
        if status.get("recommendation"):
            lines.append(f"   Рекомендация: {status.get('recommendation')}")
    if review.get("trace_log"):
        lines.append(f"Trace log: {review.get('trace_log')}")
    lines.append("")
    for row in review.get("segments") or []:
        lines.append(f"--- Segment #{row.get('index')} ---")
        lines.append(f"Original:      {row.get('original', '')}")
        lines.append(f"Raw MT:        {row.get('raw_translation', '')}")
        lines.append(f"Naturalized:   {row.get('naturalized_text', '')}")
        lines.append(f"Final:         {row.get('final_text', '')}")
        if row.get("optimized_text"):
            lines.append(f"Optimized:     {row.get('optimized_text', '')}")
        lines.append(f"Text for TTS:  {row.get('text_for_tts') or row.get('tts_text', '')}")
        if row.get("sso_level"):
            lines.append(f"SSO level:     {row.get('sso_level')}")
        if row.get("route_label") or row.get("route"):
            lines.append(f"Route:         {row.get('route_label') or row.get('route')}")
        if row.get("router_reason"):
            lines.append(f"Router reason: {row.get('router_reason')}")
        if row.get("engine"):
            lines.append(f"Engine:        {row.get('engine')}")
        if row.get("quality_score") is not None:
            lines.append(f"Quality Score: {row.get('quality_score')}")
        if row.get("english_word_count"):
            lines.append(f"English words: {row.get('english_word_count')} ({row.get('english_word_pct')}%)")
        if row.get("mixed_language_pct"):
            lines.append(f"Mixed lang:    {row.get('mixed_language_pct')}%")
        if row.get("translated_pct"):
            lines.append(f"Translated:    {row.get('translated_pct')}%")
        if row.get("semantic_adapted"):
            lines.append("Semantic adapt: yes (translation pipeline polish)")
        if row.get("optimized_text"):
            lines.append("SSO optimized: yes")
        if row.get("warnings"):
            labels = [format_warning_for_export(w) for w in row["warnings"]]
            lines.append(f"Warnings:      {', '.join(labels)}")
        lines.append("")
    return "\n".join(lines)
