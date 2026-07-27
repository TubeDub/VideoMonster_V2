"""MF6 / HOTFIX — Meaning Fit orchestration BEFORE translation LOCK / TTS.

Order: Translation UK → Meaning Fit → duration-ready → LOCK → TTS
"""

from __future__ import annotations

import logging
from typing import Any

from engines.meaning_fit.diagnostics import apply_honest_meaning_fit_reasons
from engines.meaning_fit.duration_predictor import predict_vs_slot
from engines.meaning_fit.flags import (
    meaning_fit_before_lock_flag,
    meaning_fit_expand_flag,
    meaning_fit_flag,
    meaning_fit_shorten_flag,
)
from engines.meaning_fit.score_select import select_best
from engines.meaning_fit.semantic_expand import semantic_expand
from engines.meaning_fit.semantic_shorten import semantic_shorten
from engines.meaning_fit.types import FitRequest, FitResult

logger = logging.getLogger("tubedub.engines.meaning_fit")

# Primary + safety call-sites (both MUST be before effective LOCK)
MEANING_FIT_CALL_SITE = (
    "api/auto_dub_api.py::_run_orchestrator_text_agents "
    "(apply_meaning_fit_before_lock BEFORE apply_translation_lock) "
    "+ pre-TTS safety net before lock block"
)


def fit_segment(request: FitRequest | dict[str, Any], *, force: bool = False) -> FitResult:
    if isinstance(request, dict):
        req = FitRequest(
            text_uk=str(request.get("text_uk") or ""),
            slot_ms=int(request.get("slot_ms") or 0),
            original_en=str(request.get("original_en") or ""),
            segment_id=str(request.get("segment_id") or ""),
            allow_shorten=bool(request.get("allow_shorten", True)),
            allow_expand=bool(request.get("allow_expand", True)),
            meta=dict(request.get("meta") or {}),
        )
    else:
        req = request

    text = str(req.text_uk or "").strip()
    slot = int(req.slot_ms or 0)
    if not (force or meaning_fit_flag()):
        return FitResult(
            text_uk=text,
            status="noop",
            reason="flag_off_legacy",
            slot_ms=slot,
            success=False,
            method="noop",
            meta={"enabled": False, "noop": True, "call_site": MEANING_FIT_CALL_SITE},
        )

    pred = predict_vs_slot(text, slot)
    if pred.verdict == "OK":
        return FitResult(
            text_uk=text,
            status="already_fits",
            reason="already_fits",
            predicted_ms=pred.predicted_ms,
            slot_ms=slot,
            verdict="OK",
            success=True,
            method="none",
            meta={"call_site": MEANING_FIT_CALL_SITE},
        )

    fit_lang = str((req.meta or {}).get("tgt_lang") or "uk").split("-")[0].lower()
    variants: list[dict[str, Any]] = [{"text": text, "method": "original"}]
    if pred.verdict == "TOO_LONG" and (force or meaning_fit_shorten_flag()) and req.allow_shorten:
        short = semantic_shorten(
            text,
            slot,
            original_en=req.original_en,
            force=force,
            tgt_lang=fit_lang,
        )
        if short.text_uk and short.text_uk != text:
            variants.append({"text": short.text_uk, "method": "semantic_shorten"})
        if short.success:
            short.meta["call_site"] = MEANING_FIT_CALL_SITE
            return short

    if pred.verdict == "TOO_SHORT" and (force or meaning_fit_expand_flag()) and req.allow_expand:
        expanded = semantic_expand(
            text,
            slot,
            original_en=req.original_en,
            force=force,
            tgt_lang=fit_lang,
        )
        if expanded.text_uk and expanded.text_uk != text:
            variants.append({"text": expanded.text_uk, "method": "semantic_expand"})
        if expanded.success:
            expanded.meta["call_site"] = MEANING_FIT_CALL_SITE
            return expanded

    selected = select_best(text, variants, slot)
    selected.meta["call_site"] = MEANING_FIT_CALL_SITE
    return selected


def _stamp_applied(seg: dict[str, Any], result: FitResult) -> None:
    """Reporting: applied=True for rewrite OR already_fits; attempted always."""
    seg["meaning_fit_attempted"] = True
    status = str(result.status or "")
    if status in ("paraphrase_shorten", "paraphrase_expand", "already_fits") and (
        result.success or status == "already_fits"
    ):
        seg["meaning_fit_applied"] = True
        seg["meaning_fit_reason"] = result.reason or status
        # Surface in DSAL-style review lines when DSAL itself did nothing
        if not seg.get("dsal_applied"):
            seg["dsal_skip_reason"] = str(
                seg.get("dsal_skip_reason") or "meaning_fit_owner"
            )
    elif status == "fit_failed":
        seg["meaning_fit_applied"] = False
        seg["meaning_fit_reason"] = "fit_failed"
        seg["needs_manual_review"] = True
    else:
        seg["meaning_fit_applied"] = bool(result.success)
        seg["meaning_fit_reason"] = result.reason or status


def apply_meaning_fit_before_lock(
    segments_data: list[dict[str, Any]] | None,
    task_info: dict[str, Any] | None = None,
    *,
    force: bool = False,
    call_site: str = "",
) -> dict[str, Any]:
    """Run MF on each segment by EN slot_ms. Must run BEFORE LOCK.

    Hotfix: if LOCK already set too early, temporarily unlock, run MF,
    and leave ``meaning_fit_needs_relock=True`` for the caller.
    """
    segs = list(segments_data or [])
    site = call_site or MEANING_FIT_CALL_SITE
    before_lock_ok = force or (
        meaning_fit_flag() and meaning_fit_before_lock_flag()
    )
    if not before_lock_ok:
        report = {
            "enabled": False,
            "noop": True,
            "call_site": site,
            "processed": 0,
            "flags": {
                "meaning_fit": meaning_fit_flag(),
                "before_lock": meaning_fit_before_lock_flag(),
            },
        }
        if task_info is not None:
            task_info["meaning_fit_report"] = report
            task_info["meaning_fit_before_lock"] = False
        return report

    if task_info is not None and task_info.get("meaning_fit_done"):
        return dict(task_info.get("meaning_fit_report") or {"enabled": True, "skipped": "already_done"})

    early_lock = False
    if task_info is not None and task_info.get("translation_locked"):
        # Hotfix recovery — do not skip MF after premature LOCK
        early_lock = True
        task_info["translation_locked"] = False
        task_info["meaning_fit_unlocked_for_fit"] = True
        task_info["meaning_fit_needs_relock"] = True
        logger.warning(
            "[MeaningFit] early LOCK detected — unlocked for MF (call_site=%s)",
            site,
        )

    if task_info is not None:
        task_info["meaning_fit_phase"] = "before_lock"
        task_info["meaning_fit_before_lock"] = True

    tgt_lang = str(
        (task_info or {}).get("target_lang")
        or (task_info or {}).get("tgt_lang")
        or "uk"
    ).split("-")[0].lower()

    stats = {
        "enabled": True,
        "noop": False,
        "call_site": site,
        "processed": 0,
        "shorten": 0,
        "expand": 0,
        "already_fits": 0,
        "fit_failed": 0,
        "applied": 0,
        "early_lock_recovered": early_lock,
        "tgt_lang": tgt_lang,
    }

    for seg in segs:
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        # Prefer full translation over possibly truncated TTS plain_text.
        text = str(
            seg.get("translated_text")
            or seg.get("translation_text")
            or seg.get("final_text")
            or seg.get("plain_text")
            or seg.get("text")
            or ""
        ).strip()
        if not text:
            continue
        slot = int(
            seg.get("slot_ms")
            or (
                int(seg.get("end_ms") or 0) - int(seg.get("start_ms") or 0)
            )
            or 0
        )
        # Micro-slots cannot be paraphrased into existence — mark manual, skip fail-spam
        if slot > 0 and slot < 850 and predict_vs_slot(text, slot).verdict == "TOO_LONG":
            seg["meaning_fit_source"] = text
            seg["meaning_fit_attempted"] = True
            seg["meaning_fit_applied"] = False
            seg["meaning_fit_status"] = "fit_failed"
            seg["meaning_fit_reason"] = "micro_slot_needs_merge"
            seg["needs_manual_review"] = True
            stats["processed"] += 1
            stats["fit_failed"] += 1
            continue
        seg["meaning_fit_source"] = text
        seg_lang = str(
            seg.get("target_lang") or tgt_lang or "uk"
        ).split("-")[0].lower()
        result = fit_segment(
            FitRequest(
                text_uk=text,
                slot_ms=slot,
                original_en=str(
                    seg.get("original") or seg.get("original_text") or ""
                ),
                segment_id=str(seg.get("segment_id") or ""),
                meta={"tgt_lang": seg_lang},
            ),
            force=force,
        )
        stats["processed"] += 1
        if result.status == "paraphrase_shorten":
            stats["shorten"] += 1
        elif result.status == "paraphrase_expand":
            stats["expand"] += 1
        elif result.status == "already_fits":
            stats["already_fits"] += 1
        elif result.status == "fit_failed":
            stats["fit_failed"] += 1

        if result.success and result.text_uk and result.text_uk != text:
            # Refuse destructive shorten: keep full meaning when >35% content lost
            # or meaning check fails (44.zip / Review truncation RCA).
            apply_ok = True
            try:
                from engines.semantic_meaning import (
                    is_truncated_adaptation,
                    verify_meaning_preserved,
                    word_count,
                )

                ow, nw = word_count(text), word_count(result.text_uk)
                if ow >= 8 and nw <= int(ow * 0.65):
                    apply_ok = False
                if is_truncated_adaptation(text, result.text_uk):
                    apply_ok = False
                ok_m, reason_m, _ = verify_meaning_preserved(
                    str(seg.get("original") or seg.get("original_text") or ""),
                    text,
                    result.text_uk,
                    target_lang=seg_lang,
                )
                if not ok_m and reason_m in (
                    "truncated_tail",
                    "incomplete_sentence",
                    "empty",
                ):
                    apply_ok = False
            except Exception:
                apply_ok = True
            if apply_ok:
                seg["plain_text"] = result.text_uk
                seg["final_text"] = result.text_uk
                seg["text"] = result.text_uk
                seg["tts_text"] = result.text_uk
                seg["text_for_tts"] = result.text_uk
                seg["voice_input"] = result.text_uk
                # Do NOT overwrite translated_text Raw MT anchor
            else:
                from engines.meaning_fit.types import FitResult as _FR

                seg["meaning_fit_refused_destructive"] = True
                seg["expand_required"] = True
                result = _FR(
                    text_uk=text,
                    status="rejected_truncate",
                    reason="refused_destructive_shorten",
                    predicted_ms=result.predicted_ms,
                    slot_ms=result.slot_ms,
                    verdict=result.verdict,
                    success=False,
                    needs_manual=True,
                    method="refused_destructive_shorten",
                    meta=dict(result.meta or {}),
                )

        apply_honest_meaning_fit_reasons(seg, result)
        seg["meaning_fit_method"] = result.method
        seg["predicted_tts_ms"] = result.predicted_ms
        _stamp_applied(seg, result)
        if seg.get("meaning_fit_applied"):
            stats["applied"] += 1

        overflow = max(0, int(result.predicted_ms or 0) - int(slot or 0))
        if (not result.success) and overflow >= 500:
            seg["needs_manual_review"] = True
            seg["success"] = False
            if str(seg.get("status") or "").upper() == "SUCCESS":
                seg["status"] = "FIT_FAIL"
            seg["meaning_fit_status"] = "fit_failed"
            seg["meaning_fit_applied"] = False
            seg["meaning_fit_attempted"] = True

    if task_info is not None:
        task_info["meaning_fit_report"] = stats
        task_info["meaning_fit_done"] = True
        task_info["segments_data"] = segs
    logger.info(
        "[MeaningFit] before_lock processed=%d applied=%d shorten=%d "
        "expand=%d fail=%d early_lock=%s site=%s",
        stats["processed"],
        stats["applied"],
        stats["shorten"],
        stats["expand"],
        stats["fit_failed"],
        early_lock,
        site,
    )
    return stats
