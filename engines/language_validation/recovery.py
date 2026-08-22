# -*- coding: utf-8 -*-
"""Automatic recovery before hard-stopping the dubbing pipeline."""

from __future__ import annotations

import logging
from typing import Any, Callable

from engines.language_validation.service import (
    LanguageValidationDecision,
    validate_language,
)

logger = logging.getLogger("tubedub.language_validation.recovery")

_TEXT_KEYS = (
    "text",
    "plain_text",
    "translation_text",
    "final_text",
    "final_tts_text",
    "text_for_tts",
    "tts_text",
    "approved_text",
    "voice_input",
    "semantic_text",
    "semantic_engine_text",
)


def _stamp_text(seg: dict[str, Any], text: str) -> None:
    """Write recovered spoken text onto every TTS buffer, even if the key is new.

    Zip 8fadb9dd: recovery updated ``text`` / ``final_tts_text`` but left
    ``text_for_tts`` / ``voice_input`` as mixed RU; parallel TTS spoke the stale
    buffer.
    """
    for k in _TEXT_KEYS:
        seg[k] = text
    seg["text"] = text
    seg["plain_text"] = text
    seg["tts_text"] = text
    seg["final_tts_text"] = text
    seg["text_for_tts"] = text
    seg["voice_input"] = text


def _uk_has_ru_leak(text: str, target_lang: str) -> bool:
    if str(target_lang or "").split("-")[0].lower() != "uk":
        return False
    try:
        from engines.tts_lang_lock import uk_text_has_russian_leak

        return uk_text_has_russian_leak(text)
    except Exception:
        return False


def _skip_tts_russian(seg: dict[str, Any]) -> None:
    """Drop voiced Russian so LAST-RESORT/soft-pad fills silence (Stage 29)."""
    seg["skip_tts"] = True
    seg["tts_blocked"] = True
    seg["tts_skip_reason"] = "russian_in_uk"
    seg["needs_re_tts"] = True
    seg["audio_exists"] = False
    for k in ("file", "fitted_file", "tts_file_path", "resolved_path"):
        if k in seg:
            seg[k] = None


def recover_language_issues(
    segments_data: list[dict[str, Any]],
    decisions: list[LanguageValidationDecision],
    *,
    source_segments: list[str] | None = None,
    target_lang: str,
    source_lang: str = "",
    stage: str = "",
    naturalize: bool = True,
) -> dict[str, Any]:
    """Try to heal failing segments in place.

    Pipeline: deflate phrase_loop → naturalizer → salvage → revalidate.
    Returns recovery_trace payload.
    """
    src_rows = list(source_segments or [])
    trace: list[dict[str, Any]] = []
    healed_idx: list[int] = []
    still_bad: list[LanguageValidationDecision] = []

    for d in decisions:
        if d.ok:
            continue
        idx = int(d.index if d.index is not None else -1)
        if idx < 0 or idx >= len(segments_data):
            still_bad.append(d)
            continue
        seg = segments_data[idx]
        if not isinstance(seg, dict):
            still_bad.append(d)
            continue

        original = src_rows[idx] if idx < len(src_rows) else ""
        current = str(seg.get("text") or seg.get("plain_text") or "").strip()
        actions: list[str] = []
        candidate = current
        if original and not str(seg.get("original_text") or "").strip():
            seg["original_text"] = original

        def _ru_leak(text: str) -> bool:
            return _uk_has_ru_leak(text, target_lang)

        # 0) EN→UK remt when STUDIO saw Russian (diag d3b6fe76 idx 27)
        if _ru_leak(candidate) and original.strip():
            try:
                from engines.tts_lang_lock import force_remt_segment_no_cache

                remt = force_remt_segment_no_cache(
                    original,
                    src_lang=source_lang or "en",
                    tgt_lang="uk",
                )
                if remt and remt.strip() and not _ru_leak(remt):
                    candidate = remt.strip()
                    actions.append("remt_en_uk")
            except Exception as exc:
                actions.append(f"remt_failed:{exc}")
        if _ru_leak(candidate):
            try:
                from engines.tts_lang_lock import rewrite_russian_leak_for_uk

                rewritten = rewrite_russian_leak_for_uk(candidate)
                if rewritten and rewritten != candidate and not _ru_leak(rewritten):
                    candidate = rewritten
                    actions.append("ruism_rewrite")
            except Exception as exc:
                actions.append(f"ruism_failed:{exc}")

        # 1) Phrase-loop deflate
        if d.category in ("phrase_loop", "meaning_collapse") or "phrase_loop" in d.reasons:
            try:
                from engines.mt.cross_script_guard import (
                    deflate_phrase_loop,
                    has_phrase_loop,
                )

                if has_phrase_loop(candidate):
                    deflated = deflate_phrase_loop(candidate)
                    if deflated and deflated != candidate:
                        candidate = deflated
                        actions.append("deflate_phrase_loop")
            except Exception as exc:
                actions.append(f"deflate_failed:{exc}")

        # 2) Naturalizer pass
        if naturalize and candidate:
            try:
                from engines.translation_naturalizer import naturalize_text

                nat = naturalize_text(candidate, target_lang)
                if nat and nat.strip() and nat.strip() != candidate:
                    candidate = nat.strip()
                    actions.append("naturalizer")
            except Exception as exc:
                actions.append(f"naturalizer_failed:{exc}")

        # 3) Salvage (scrub / gloss / offline)
        try:
            from engines.pipeline_language_gate import salvage_collapsed_segment_text

            fixed, method = salvage_collapsed_segment_text(
                text=candidate,
                original=original,
                approved=str(seg.get("approved_text") or ""),
                target_lang=target_lang,
                source_lang=source_lang,
            )
            if fixed and fixed.strip():
                if fixed.strip() != candidate:
                    actions.append(f"salvage:{method}")
                candidate = fixed.strip()
        except Exception as exc:
            actions.append(f"salvage_failed:{exc}")

        # 4) Revalidate
        neighbors = []
        if idx > 0:
            neighbors.append(
                str(
                    (segments_data[idx - 1] or {}).get("text")
                    if isinstance(segments_data[idx - 1], dict)
                    else ""
                )
            )
        if idx + 1 < len(segments_data) and isinstance(segments_data[idx + 1], dict):
            neighbors.append(str(segments_data[idx + 1].get("text") or ""))

        recheck = validate_language(
            candidate,
            target_lang=target_lang,
            original=original,
            source_lang=source_lang,
            stage=stage or "recovery",
            index=idx,
            segment_id=d.segment_id,
            neighbor_texts=neighbors,
            allow_semantic_soft_pass=True,
        )
        actions.append("revalidate")
        recheck.recovery_actions = list(d.recovery_actions) + actions
        recheck.decision_trace = list(d.decision_trace) + [
            {"step": "recovery", "actions": actions, "candidate_preview": candidate[:160]}
        ] + list(recheck.decision_trace)

        row = {
            "index": idx,
            "before_category": d.category,
            "before_code": d.code,
            "actions": actions,
            "after_ok": recheck.ok,
            "after_category": recheck.category,
            "after_code": recheck.code,
            "after_confidence": recheck.confidence,
            "text_preview": candidate[:200],
        }
        trace.append(row)

        if recheck.ok and not _ru_leak(candidate):
            _stamp_text(seg, candidate)
            seg["language_recovery"] = {
                "healed": True,
                "actions": actions,
                "from": d.category,
            }
            healed_idx.append(idx)
            continue

        # Glue / это→це must not count as healed while Russian remains
        # (zip idx 33: «Честно говоря, це много, саме тоді.» fooled classifier).
        if recheck.ok and _ru_leak(candidate):
            recheck.ok = False
            recheck.category = recheck.category or "low_confidence"
            actions.append("reject_false_uk_glue_heal")

        # Persist best candidate even if still flagged (helps next stage)
        if candidate and candidate != current:
            _stamp_text(seg, candidate)
            seg["language_recovery"] = {
                "healed": False,
                "actions": actions,
                "from": d.category,
                "remaining": recheck.category,
            }
        still_bad.append(recheck)

    # Final hard_fail promotion only after recovery exhausted
    hard: list[LanguageValidationDecision] = []
    soft: list[LanguageValidationDecision] = []
    for d in still_bad:
        idx = int(d.index if d.index is not None else -1)
        leftover = ""
        if 0 <= idx < len(segments_data) and isinstance(segments_data[idx], dict):
            leftover = str(
                segments_data[idx].get("text")
                or segments_data[idx].get("plain_text")
                or d.text_checked
                or ""
            )
        ru_in_uk = str(target_lang or "").split("-")[0].lower() == "uk" and (
            d.detected_lang == "ru" or _uk_has_ru_leak(leftover, target_lang)
        )
        if ru_in_uk and 0 <= idx < len(segments_data) and isinstance(
            segments_data[idx], dict
        ):
            # Zip d3b6fe76: recovery_exhausted_foreign_lang bricked STUDIO.
            # Pad the hole instead of killing the job after remt/rewrite failed.
            _skip_tts_russian(segments_data[idx])
            d.hard_fail = False
            d.reasons = list(d.reasons) + ["skip_tts_russian_in_uk"]
            d.recovery_actions = list(d.recovery_actions) + ["skip_tts_pad"]
            soft.append(d)
            continue
        if d.category == "language_mismatch" and d.hard_fail:
            hard.append(d)
            continue
        if d.category == "meaning_collapse":
            critical = any(
                "critical_cue" in r or "pregnancy" in r or "source_script" in r
                for r in d.reasons
            )
            if critical:
                d.hard_fail = True
                hard.append(d)
                continue
            if d.detected_lang == d.expected_lang or d.target_confidence >= 0.72:
                d.ok = True
                d.hard_fail = False
                d.category = "pass"
                d.reasons = list(d.reasons) + ["continue_after_recovery_lang_ok"]
                d.recovery_actions = list(d.recovery_actions) + [
                    "soft_continue_target_language_ok"
                ]
                if d.index is not None and 0 <= d.index < len(segments_data):
                    healed_idx.append(int(d.index))
                continue
            soft.append(d)
            continue
        if d.category in ("low_confidence", "ambiguous"):
            # Foreign language with high confidence after recovery → hard stop
            if (
                d.detected_lang
                and d.detected_lang != d.expected_lang
                and d.confidence >= 0.7
                and d.target_confidence < 0.35
            ):
                from engines.language_validation.service import format_validation_message

                d.hard_fail = True
                d.category = "language_mismatch"
                d.code = d.code or "language_mismatch_after_recovery"
                d.reasons = list(d.reasons) + ["recovery_exhausted_foreign_lang"]
                d.recovery_actions = list(d.recovery_actions) + [
                    "recovery_exhausted"
                ]
                d.message = format_validation_message(d)
                hard.append(d)
            else:
                soft.append(d)
            continue
        soft.append(d)

    return {
        "healed_indices": sorted(set(healed_idx)),
        "still_hard": [x.to_issue() for x in hard],
        "still_soft": [x.to_issue() for x in soft],
        "trace": trace,
        "recovered": len(set(healed_idx)),
        "failed_hard": len(hard),
    }


def apply_recovery_and_revalidate(
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str,
    source_lang: str = "",
    stage: str = "",
    on_healed: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Full validate → recover → revalidate cycle."""
    from engines.language_validation.service import validate_segments

    first = validate_segments(
        segments_data,
        source_segments=source_segments,
        target_lang=target_lang,
        source_lang=source_lang,
        stage=stage,
    )
    if not first:
        return {
            "healed_indices": [],
            "still_hard": [],
            "still_soft": [],
            "trace": [],
            "recovered": 0,
            "failed_hard": 0,
            "initial_issues": 0,
            "decisions": [],
        }

    result = recover_language_issues(
        segments_data,
        first,
        source_segments=source_segments,
        target_lang=target_lang,
        source_lang=source_lang,
        stage=stage,
    )
    result["initial_issues"] = len(first)
    result["decisions"] = [d.to_dict() for d in first]

    if on_healed:
        for idx in result.get("healed_indices") or []:
            if 0 <= idx < len(segments_data) and isinstance(segments_data[idx], dict):
                try:
                    on_healed(idx, segments_data[idx])
                except Exception as exc:
                    logger.warning("on_healed idx=%s failed: %s", idx, exc)

    return result
