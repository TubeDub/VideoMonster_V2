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


_UNSAFE_REASON_CODES = frozenset(
    {
        "meaning_collapse",
        "cjk_meaning_collapse",
        "source_script_leak",
        "meaning_loss",
        "meaning_collapse",
    }
)


def _tts_blocked(seg: dict | None, audit: dict | None = None) -> bool:
    """True when MT failure must not be shown/voiced as Final/TTS."""
    s = seg or {}
    a = audit or {}
    if s.get("tts_blocked") or s.get("skip_tts") or a.get("tts_blocked"):
        return True
    reasons = set()
    for src in (
        s.get("tps_reason_codes"),
        a.get("reason_codes"),
        s.get("dirty_reasons"),
        (s.get("trh") or {}).get("reason_codes") if isinstance(s.get("trh"), dict) else None,
        (s.get("trh") or {}).get("dirty_reasons") if isinstance(s.get("trh"), dict) else None,
        a.get("dirty_reasons"),
    ):
        if isinstance(src, (list, tuple, set)):
            reasons.update(str(x) for x in src if x)
    for w in a.get("validation_warnings") or []:
        if isinstance(w, dict) and w.get("code"):
            reasons.add(str(w.get("code")))
    if reasons & _UNSAFE_REASON_CODES:
        # Collapse / script-leak codes always block TTS — even if TPS wrongly
        # stamped PASS with a non-empty approved_text (see _tmp_3333).
        return True
    # Live detect: collapsed MT still sitting in nat/raw while Final empty
    try:
        from engines.mt.cross_script_guard import meaning_collapse, source_script_leak

        original = str(
            s.get("source_text")
            or a.get("original")
            or a.get("whisper_text")
            or ""
        )
        candidate = str(
            s.get("rejected_translation")
            or a.get("naturalized_text")
            or a.get("raw_translation")
            or s.get("naturalized_text")
            or ""
        ).strip()
        final_now = str(s.get("final_text") or s.get("text") or a.get("final_text") or "").strip()
        if candidate and not final_now and (
            meaning_collapse(original, candidate)
            or source_script_leak(original, candidate)
        ):
            return True
    except Exception:
        pass
    return False


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


def _first_plain_text(*sources: dict[str, Any], keys: tuple[str, ...]) -> str:
    for source in sources:
        if not source:
            continue
        for key in keys:
            val = str(source.get(key) or "").strip()
            if val and not _is_ssml(val):
                return val
    return ""


def _resolve_semantic_text(seg: dict, audit: dict) -> str:
    """Post-MT semantic polish — audits use semantic_text; diagnostics use semantic_engine_text."""
    semantic = _first_plain_text(
        audit,
        seg,
        keys=("semantic_engine_text", "semantic_text"),
    )
    if semantic:
        return semantic
    chain = (audit.get("quality_details") or {}).get("transformation_chain") or {}
    for key in ("semantic", "baseline", "semantic_text"):
        val = str(chain.get(key) or "").strip()
        if val and not _is_ssml(val):
            return val
    return ""


def _resolve_final_text(seg: dict, audit: dict) -> str:
    from engines.translation_validation import (
        prefer_semantic_authority,
        resolve_post_quality_text,
        texts_equivalent_for_ownership,
    )

    nat = str(
        (audit or {}).get("naturalized_text")
        or (seg or {}).get("naturalized_text")
        or ""
    ).strip()

    def _heal(text: str) -> str:
        try:
            from engines.trh import heal_truncated_final

            return heal_truncated_final(text, nat)
        except Exception:
            return text

    def _soft_blocked_nat_ok(candidate: str) -> bool:
        """Allow healed UK nat when TTS was wiped only for healable phrase loops."""
        if not candidate or _is_ssml(candidate):
            return False
        reasons = set(
            (seg or {}).get("tps_reason_codes")
            or (audit or {}).get("reason_codes")
            or []
        )
        block_reason = str((seg or {}).get("tts_blocked_reason") or "")
        soft_reasons = {"phrase_loop", "meaning_collapse", "dirty_mt_noop"}
        if reasons and not reasons <= soft_reasons and block_reason not in soft_reasons:
            if block_reason not in ("", "phrase_loop", "meaning_collapse"):
                return False
            if reasons & {"source_script_leak", "cjk_meaning_collapse", "meaning_loss"}:
                return False
        try:
            from engines.mt.cross_script_guard import (
                has_phrase_loop,
                meaning_collapse,
                source_script_leak,
            )

            src = str(
                (seg or {}).get("original_text")
                or (seg or {}).get("source_text")
                or audit.get("whisper_text")
                or ""
            )
            if has_phrase_loop(candidate, min_repeats=2):
                return False
            if source_script_leak(src, candidate):
                return False
            hit = meaning_collapse(src, candidate) if src else None
            if hit and set(hit.get("reasons") or []) - {"phrase_loop"}:
                return False
            return True
        except Exception:
            return bool(candidate)

    # Blocked hallucination / wrong-script: never resurrect dirty MT,
    # but do surface a clean healed naturalized after phrase-loop wipe.
    if _tts_blocked(seg, audit):
        if _soft_blocked_nat_ok(nat):
            return _heal(nat)
        raw_blocked = str(audit.get("raw_translation") or "").strip()
        if _soft_blocked_nat_ok(raw_blocked):
            return _heal(raw_blocked)
        return ""

    # After hard audio trim: Final must equal spoken prefix (not uncut paragraph).
    if bool(
        (seg or {}).get("voice_truncated")
        or audit.get("voice_truncated")
        or ((seg or {}).get("timing_meta") or {}).get("speech_trimmed")
    ):
        spoken = _first_plain_text(
            audit,
            seg,
            keys=("spoken_fit_text", "tts_text", "plain_text", "final_text"),
        )
        if not spoken:
            spoken = str(((seg or {}).get("timing_meta") or {}).get("spoken_fit_text") or "").strip()
        if spoken and not _is_ssml(spoken):
            return _heal(spoken)

    # TPS Single Approved Text
    approved = str(
        (seg or {}).get("approved_text") or audit.get("approved_text") or ""
    ).strip()
    if approved and not _is_ssml(approved):
        return _heal(approved)

    raw = str(audit.get("raw_translation") or "").strip()
    semantic = _resolve_semantic_text(seg, audit)
    merged = dict(seg or {})
    if semantic and not merged.get("semantic_text"):
        merged["semantic_text"] = semantic
    if semantic and not merged.get("semantic_engine_text"):
        merged["semantic_engine_text"] = semantic
    final = resolve_post_quality_text(merged, audit)
    if final:
        return _heal(final)
    # Fallback for sparse segment rows without post-quality fields.
    legacy = _first_plain_text(
        audit,
        seg,
        keys=("final_text", "pre_tts_text", "voice_input"),
    )
    if legacy:
        if prefer_semantic_authority(semantic=semantic, candidate=legacy, raw_mt=raw):
            return _heal(semantic)
        if raw and texts_equivalent_for_ownership(legacy, raw) and semantic and not texts_equivalent_for_ownership(semantic, raw):
            return _heal(semantic)
        return _heal(legacy)
    if semantic:
        return _heal(semantic)
    for key in ("plain_text", "translation_text", "translated_text", "grammar_text", "timing_text"):
        val = str(seg.get(key) or "").strip()
        if val and not _is_ssml(val):
            if prefer_semantic_authority(semantic=semantic, candidate=val, raw_mt=raw):
                return _heal(semantic)
            return _heal(val)
    val = str(seg.get("text") or "").strip()
    if val and not _is_ssml(val):
        return _heal(val)
    # Manual FAIL: still show clean healed nat (phrase-loop wipe recovery).
    tqe = str(
        (seg or {}).get("tqe_status") or audit.get("tqe_status") or ""
    ).upper()
    if "FAIL" in tqe or (seg or {}).get("needs_manual_review"):
        if _soft_blocked_nat_ok(nat):
            return _heal(nat)
        if _soft_blocked_nat_ok(raw):
            return _heal(raw)
        return ""
    return nat if nat and not _is_ssml(nat) else ""


def _resolve_text_for_tts(seg: dict, audit: dict, *, final: str, tts_synthesized: bool) -> str:
    """UI/TTS bound text — before synthesis equals final; after synthesis equals spoken text.

    Never return SSML markup to the Review UI (it confuses operators and is not
    the spoken plain string). Stress marks are stripped for display.
    """
    if _tts_blocked(seg, audit):
        # Match Final recovery: healed nat after phrase-loop wipe is voiceable.
        if final:
            return final
        return ""

    def _clean(val: str) -> str:
        s = str(val or "").strip()
        if not s:
            return ""
        if s.lstrip().startswith("<speak") or "<" in s and "emphasis" in s:
            import re

            s = re.sub(r"<[^>]+>", " ", s)
            s = re.sub(r"[ \t]+", " ", s).strip()
        try:
            from engines.stress_marks import strip_stress_marks

            s = strip_stress_marks(s)
        except Exception:
            pass
        return s

    if not tts_synthesized:
        return _clean(final) or final
    for key in ("tts_text", "plain_text", "spoken_fit_text"):
        val = _clean(
            str(
                audit.get(key)
                or seg.get(key)
                or ((seg.get("timing_meta") or {}).get(key) if key == "spoken_fit_text" else "")
                or ""
            )
        )
        if val and not _is_ssml(val):
            # After hard audio trim (trim_overlap), spoken is shorter on purpose —
            # never resurrect the uncut Final (Review must match what plays).
            try:
                from engines.tts_audio_text_sync import prefer_spoken_over_longer_final

                chosen = prefer_spoken_over_longer_final(
                    final=final, spoken=val, seg=seg, audit=audit
                )
                if chosen == val:
                    return val
            except Exception:
                pass
            # Prefer Final when TTS was destructively shortened (filler drop /
            # shared-blob chop) so Review does not show a lie vs approved Final.
            # Skip this bias when voice was truncated to the slot.
            voice_cut = bool(
                seg.get("voice_truncated")
                or audit.get("voice_truncated")
                or ((seg.get("timing_meta") or {}).get("speech_trimmed"))
            )
            if not voice_cut:
                try:
                    from engines.semantic_meaning import is_truncated_adaptation

                    if final and is_truncated_adaptation(final, val):
                        return _clean(final) or final
                except Exception:
                    pass
            return val
    return _clean(final) or final


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
        # Mutate the live audit row so QA fingerprint / warning cache persist.
        audit = audit_by_idx.get(i)
        if not isinstance(audit, dict):
            audit = {}
            audit_by_idx[i] = audit
            if isinstance(audits, list):
                audits.append(audit)
                audit["index"] = i
        seg = (
            dict(segments_data[i])
            if i < len(segments_data) and isinstance(segments_data[i], dict)
            else {}
        )
        original = str(src or audit.get("whisper_text") or "")
        audit.setdefault("original", original)
        seg.setdefault("source_text", original)
        trh = seg.get("trh") if isinstance(seg.get("trh"), dict) else {}
        if trh:
            audit.setdefault("reason_codes", trh.get("reason_codes") or [])
            audit.setdefault("dirty_reasons", trh.get("dirty_reasons") or [])
            audit.setdefault("tqe_status", trh.get("tqe_status") or "")
            if trh.get("tps_path") == "manual" or "FAIL" in str(
                trh.get("tqe_status") or ""
            ).upper():
                seg.setdefault("needs_manual_review", True)
        raw = str(audit.get("raw_translation") or "")
        semantic = _resolve_semantic_text(seg, audit)
        final = _resolve_final_text(seg, audit)
        naturalized = str(audit.get("naturalized_text") or "").strip()
        if _is_ssml(naturalized):
            naturalized = ""
        if not naturalized:
            naturalized = semantic or final
        elif (
            semantic
            and raw.strip()
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

        _qs_raw = audit.get("quality_score")
        try:
            _qs_num = float(_qs_raw) if _qs_raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            _qs_num = 0.0
        try:
            _qd_num = float(qd.get("quality_score") or 0)
        except (TypeError, ValueError):
            _qd_num = 0.0
        _need_recompute = (
            bool(raw.strip())
            and (
                not qd
                or _qs_raw in (None, "", 0, 0.0, "0", "0.0")
                or _qs_num <= 0
                or _qd_num <= 0
            )
        )
        if _need_recompute:
            try:
                from engines.quality_score_v2 import compute_quality_score_v2

                computed_score, computed_qd = compute_quality_score_v2(
                    original,
                    raw,
                    src_lang=src_lang,
                    tgt_lang=tgt_lang,
                    naturalized=naturalized or final or text_for_tts,
                )
            except Exception:
                from engines.translation_quality_score import compute_quality_score

                computed_score, computed_qd = compute_quality_score(
                    original,
                    naturalized or final or raw,
                    src_lang=src_lang,
                    tgt_lang=tgt_lang,
                )
            # Computed wins over stale zeros / empty placeholders in old qd.
            qd = {**qd, **(computed_qd or {})}
            if computed_score and (
                _qs_raw in (None, "", 0, 0.0, "0", "0.0") or _qs_num <= 0
            ):
                audit["quality_score"] = computed_score
                audit["quality_details"] = qd

        try:
            quality_score = float(
                audit.get("quality_score") or qd.get("quality_score") or 0
            )
        except (TypeError, ValueError):
            quality_score = 0.0
        # Dim values may be 0–1 fractions — scale for Review display
        if 0 < quality_score <= 1.0:
            quality_score = round(quality_score * 100.0, 1)

        slot_ms_val = float(seg.get("slot_ms") or audit.get("duration_ms") or 0)
        playback_ms_val = float(
            seg.get("playback_duration") or seg.get("tts_ms") or 0
        )
        try:
            from engines.translation_review_diagnostics import (
                build_segment_diagnostics,
                quality_breakdown,
            )

            diagnostics = build_segment_diagnostics(
                seg=seg,
                audit=audit,
                text=final or text_for_tts,
                original=original,
                slot_ms=slot_ms_val,
                tts_ms=playback_ms_val,
                tgt_lang=str(tgt_lang or "uk"),
                warnings=warnings,
            )
            entity_ok = not diagnostics.get("entity_risk")
            q_break = quality_breakdown(
                quality_score=quality_score,
                quality_analysis=quality_analysis if isinstance(quality_analysis, dict) else {},
                duration_match_score=int(
                    seg.get("duration_match_score")
                    or (seg.get("text_adaptation_trace") or {}).get("duration_match_score")
                    or 0
                ),
                qd=qd if isinstance(qd, dict) else {},
                entity_ok=entity_ok,
            )
        except Exception:
            diagnostics = {}
            q_break = {
                "translation": round(quality_score, 1),
                "naturalness": round(quality_score, 1),
                "entities": 100.0,
                "timing": 0.0,
                "tts": 0.0,
                "overall": round(quality_score, 1),
            }

        # Review honesty for already-muxed tasks: if speech was hard-trimmed to
        # the slot, show the spoken prefix — not the uncut paragraph.
        try:
            from engines.tts_audio_text_sync import estimate_spoken_prefix

            slot_i = int(slot_ms_val or 0)
            tts_i = int(
                playback_ms_val
                or seg.get("tts_ms")
                or (diagnostics or {}).get("tts_ms")
                or 0
            )
            overflow_i = int(
                (diagnostics or {}).get("overflow_ms")
                or seg.get("overflow_ms")
                or 0
            )
            voice_cut = bool(
                (diagnostics or {}).get("voice_truncated")
                or seg.get("voice_truncated")
                or ((seg.get("timing_meta") or {}).get("speech_trimmed"))
                or (overflow_i > 120 and tts_i > slot_i > 0)
            )
            if voice_cut and final and tts_i > slot_i > 0:
                spoken_ms = max(1, min(slot_i, tts_i - overflow_i if overflow_i else slot_i))
                spoken_prefix = estimate_spoken_prefix(
                    final, tts_ms=tts_i, spoken_ms=spoken_ms
                )
                if spoken_prefix and len(spoken_prefix) + 8 < len(final):
                    final = spoken_prefix
                    text_for_tts = spoken_prefix
        except Exception:
            pass

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
                "playback_duration_ms": playback_ms_val,
                "slot_ms": slot_ms_val,
                "diagnostics": diagnostics,
                "fill_pct": diagnostics.get("fill_pct", 0),
                "fill_status": diagnostics.get("fill_status", "green"),
                "status_label": diagnostics.get("status_label", ""),
                "seg_advice": diagnostics.get("seg_advice", ""),
                "seg_status": diagnostics.get("seg_status", ""),
                "expected_tts_ms": diagnostics.get("expected_tts_ms", 0)
                or int(seg.get("predicted_tts_ms") or 0),
                "word_count": diagnostics.get("word_count", 0),
                "overflow_ms": diagnostics.get("overflow_ms", 0)
                or int(seg.get("predicted_overflow_ms") or 0),
                "tts_ms": diagnostics.get("tts_ms", int(playback_ms_val)),
                # Stage 17 Review/trace: slot / tts / dead air / voice.
                "dead_air_ms": int(
                    seg.get("dead_air_ms")
                    or (diagnostics or {}).get("dead_air_ms")
                    or 0
                ),
                "voice_id": str(
                    seg.get("voice_id")
                    or seg.get("assigned_voice")
                    or seg.get("voice")
                    or ""
                ),
                # Stage 19: slot-fill strategy visible in Review.
                "fill_ratio": float(
                    seg.get("fill_ratio")
                    or (seg.get("text_slot_fit") or {}).get("fill_ratio")
                    or 0
                ),
                "atempo": float(
                    seg.get("atempo")
                    or (seg.get("text_slot_fit") or {}).get("atempo")
                    or 1.0
                ),
                "strategy": str(
                    seg.get("slot_strategy")
                    or (seg.get("text_slot_fit") or {}).get("strategy")
                    or (seg.get("text_slot_fit") or {}).get("action")
                    or ""
                ),
                "tts_text_hash": str(seg.get("tts_text_hash") or ""),
                "predicted_tts_ms": int(
                    seg.get("predicted_tts_ms")
                    or (seg.get("text_slot_fit") or {}).get("predicted_tts_ms")
                    or 0
                ),
                "slot_budget_ms": int(
                    (seg.get("slot_budget") or {}).get("slot_ms")
                    or slot_ms_val
                    or 0
                ),
                "safety_margin_ms": int(
                    seg.get("safety_margin_ms")
                    or (seg.get("slot_budget") or {}).get("safety_margin_ms")
                    or max(
                        0,
                        int(slot_ms_val)
                        - int(
                            diagnostics.get("expected_tts_ms")
                            or seg.get("predicted_tts_ms")
                            or playback_ms_val
                            or 0
                        ),
                    )
                ),
                "original_char_len": len(original),
                "translation_char_len": len(final or text_for_tts or ""),
                "estimated_speech_ms": int(
                    diagnostics.get("expected_tts_ms")
                    or seg.get("predicted_tts_ms")
                    or diagnostics.get("tts_ms")
                    or 0
                ),
                "sync_status": str(seg.get("sync_status") or ""),
                "text_adaptation_reason": str(
                    seg.get("text_adaptation_reason") or ""
                ),
                "audio_strategy_reason": str(
                    seg.get("audio_strategy_reason") or ""
                ),
                "residual_overflow_ms": int(
                    seg.get("residual_overflow_ms")
                    or (
                        max(
                            0,
                            int(
                                seg.get("playback_duration")
                                or seg.get("tts_ms")
                                or 0
                            )
                            - int(seg.get("slot_ms") or 0),
                        )
                        if int(seg.get("slot_ms") or 0) > 0
                        else int(seg.get("predicted_overflow_ms") or 0)
                    )
                ),
                "slot_strategy_reason": str(
                    seg.get("slot_strategy_reason")
                    or (seg.get("slot_budget") or {}).get("reason")
                    or ""
                ),
                "scheduler_reason": str(seg.get("scheduler_reason") or ""),
                "adaptation_uuid": str(seg.get("adaptation_uuid") or ""),
                "translation_uuid": str(seg.get("translation_uuid") or ""),
                "segment_id": str(seg.get("segment_id") or ""),
                "text_fits": diagnostics.get("text_fits", final),
                "text_overflow": diagnostics.get("text_overflow", ""),
                "algorithms": diagnostics.get("algorithms") or [],
                "quality_breakdown": q_break,
                "speech_end": diagnostics.get("speech_end") or {},
                "meaning_loss_risk": bool(diagnostics.get("meaning_loss_risk")),
                "entity_risk": bool(diagnostics.get("entity_risk")),
                "voice_truncated": bool(diagnostics.get("voice_truncated")),
                "voice_finished_naturally": bool(
                    diagnostics.get("voice_finished_naturally", True)
                ),
                "manual_review_required": bool(
                    diagnostics.get("manual_review_required")
                    or seg.get("needs_manual_review")
                ),
                "dsal_delta_ms": int(
                    seg.get("dsal_delta_ms")
                    or (seg.get("text_adaptation_trace") or {}).get("speech_difference_ms")
                    or 0
                ),
                "dsal_band": str(
                    seg.get("dsal_band")
                    or (seg.get("text_adaptation_trace") or {}).get("dsal_band")
                    or ""
                ),
                "dsal_applied": bool(
                    seg.get("dsal_applied")
                    or (seg.get("text_adaptation_trace") or {}).get("executed")
                    or any(
                        str(stage).startswith("dsal")
                        for stage in (seg.get("adaptation_stages") or [])
                    )
                ),
                "meaning_fit_applied": bool(seg.get("meaning_fit_applied")),
                "meaning_fit_attempted": bool(seg.get("meaning_fit_attempted")),
                "meaning_fit_status": str(seg.get("meaning_fit_status") or ""),
                "meaning_fit_reason": str(
                    seg.get("meaning_fit_reason")
                    or seg.get("text_adaptation_reason")
                    or ""
                ),
                "dsal_skip_reason": str(
                    seg.get("dsal_skip_reason")
                    or (seg.get("text_adaptation_trace") or {}).get("dsal_skip_reason")
                    or task_info.get("dsal_skip_reason")
                    or ""
                ),
                "trh": seg.get("trh") or audit.get("trh") or {},
                "dirty_mt_score": audit.get("dirty_mt_score")
                or (seg.get("trh") or {}).get("dirty_mt_score"),
                "naturalizer_skip_reason": str(
                    audit.get("naturalizer_skip_reason")
                    or (seg.get("trh") or {}).get("naturalizer_skip_reason")
                    or ""
                ),
                "retry_count": int(
                    audit.get("retry_count")
                    or (seg.get("trh") or {}).get("retry_count")
                    or 0
                ),
                "judge_used": bool(
                    audit.get("judge_used")
                    or (seg.get("trh") or {}).get("judge_used")
                ),
                "duration_match_score": int(
                    seg.get("duration_match_score")
                    or (seg.get("text_adaptation_trace") or {}).get("duration_match_score")
                    or 0
                ),
                "clause_coverage": float(seg.get("clause_coverage") or 0),
                "expand_required": bool(seg.get("expand_required")),
                "needs_studio": bool(
                    seg.get("needs_studio") or task_info.get("needs_studio")
                ),
                "needs_manual_review": bool(
                    seg.get("needs_manual_review")
                    or diagnostics.get("manual_review_required")
                    or diagnostics.get("voice_truncated")
                    or i in set(task_info.get("tps_manual_indices") or [])
                ),
                "tqe_status": str(seg.get("tqe_status") or audit.get("tqe_status") or ""),
                "tps_path": str(seg.get("tps_path") or audit.get("tps_path") or ""),
                "approved_text": str(seg.get("approved_text") or "").strip(),
                "lock_gate_ok": seg.get("lock_gate_ok"),
                "lock_gate_failed": seg.get("lock_gate_failed"),
                "translation_locked": bool(seg.get("translation_locked")),
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
        # Stage 17: post-mux silence vs EN speech mask.
        "dead_air_regions": list(task_info.get("dead_air_regions") or []),
        "dead_air_audit": task_info.get("dead_air_audit"),
        "dead_air_warning": str(task_info.get("dead_air_warning") or ""),
        "needs_studio": bool(task_info.get("needs_studio")),
        "translation_lock_deferred": bool(task_info.get("translation_lock_deferred")),
        "tps": bool(task_info.get("tps")),
        "tps_manual_indices": list(task_info.get("tps_manual_indices") or []),
        "tps_metrics": task_info.get("tps_metrics"),
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
        if row.get("slot_ms"):
            lines.append(
                f"Slot/Delta/Band: {int(row.get('slot_ms') or 0)}ms / "
                f"{int(row.get('dsal_delta_ms') or 0)}ms / "
                f"{row.get('dsal_band') or '—'}"
            )
        if row.get("dsal_applied") is not None or row.get("meaning_fit_attempted"):
            lines.append(
                f"DSAL: applied={row.get('dsal_applied')} "
                f"match={row.get('duration_match_score')} "
                f"clause={row.get('clause_coverage')} "
                f"expand_required={row.get('expand_required')}"
            )
        if row.get("meaning_fit_attempted") or row.get("meaning_fit_status"):
            lines.append(
                f"Meaning Fit: applied={row.get('meaning_fit_applied')} "
                f"status={row.get('meaning_fit_status') or '—'} "
                f"reason={row.get('meaning_fit_reason') or '—'}"
            )
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
