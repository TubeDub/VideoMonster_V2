"""PSA4 — Segment Normalizer (Pipeline Stability v2).

Merges micro-slots (< MIN_SLOT_MS), fragments ("And at"), and mid-name
false cuts («George Jr.» / «Джордж-молодший.»).
Boundary changes → NEW segment_ids via PSA3 (not edit-in-place).
Flag: VM_FLAG_SEGMENT_NORMALIZER (default OFF → legacy).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.pipeline_integrity.segment_normalizer")

MIN_SLOT_MS = 850
# ba6ec #7: Whisper often emits exact 1000ms cuts with long text
WHISPER_CUT_MS = 1000

# Named entities / phrases that must not be split (EN + UK)
_PROTECTED_PHRASES = (
    r"George\s+Jr\.?",
    r"Джордж[-\s]?молодш\w*",
    r"University\s+of\s+Southern\s+California",
    r"Star\s+Wars",
    r"Ю\s*Ес\s*Сі",
    r"USC",
)
_PROTECTED_RE = re.compile("|".join(f"(?:{p})" for p in _PROTECTED_PHRASES), re.I)
_NE_SPAN_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|"
    r"[A-Z]{2,}|"
    r"\d{1,4}(?:[./-]\d{1,2}){1,2}|"
    r"\d+(?:[.,]\d+)?\s*(?:km|kg|%|mph)?)\b"
)
# Mid-name false sentence end (EN Jr. / UK «Джордж-молодший.»)
_MID_NAME_END_RE = re.compile(
    r"(?i)(?:\b(?:George\s+)?Jr|Джордж[-\s]?молодш\w*)\.\s*$"
)
# Tiny incomplete fragments that must merge (ba6ec "And at")
_FRAGMENT_RE = re.compile(
    r"(?i)^(and\s+at|and\s+so|so\s+two|but\s+as|that\s+point|"
    r"і\s+в|а\s+ле|тож|так\s+як)\b.*$"
)


def _t_start(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("start", 0))
    if isinstance(item, (list, tuple)) and item:
        return int(item[0])
    return 0


def _t_end(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("end", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[1])
    return 0


def _contains_protected(text: str) -> bool:
    t = str(text or "")
    return bool(_PROTECTED_RE.search(t) or _NE_SPAN_RE.search(t))


def _word_count(text: str) -> int:
    return len([w for w in str(text or "").split() if w])


def is_micro_or_fragment(
    text: str,
    slot_ms: int,
    *,
    min_ms: int = MIN_SLOT_MS,
) -> bool:
    """True when slot must be merged before TTS (PSA4)."""
    t = str(text or "").strip()
    dur = max(0, int(slot_ms or 0))
    if not t:
        return dur < min_ms
    if dur < min_ms:
        return True
    wc = _word_count(t)
    # Ultra-short fragments regardless of slight over-min duration
    if wc <= 3 and dur <= max(min_ms + 200, 1200):
        return True
    # Discourse openers ("And at", "So two weeks earlier") must merge even when
    # Whisper assigned a long bogus slot — otherwise MT/TTS bleeds onto both rows.
    if _FRAGMENT_RE.match(t) and t[-1] not in ".!?…":
        return True
    if _FRAGMENT_RE.match(t) and dur < 2000:
        return True
    # Long text crammed into Whisper ~1000ms cut (ba6ec #7)
    if dur <= WHISPER_CUT_MS and wc >= 8:
        return True
    # Mid-name dangling end without continuation yet — still a fragment risk
    if _MID_NAME_END_RE.search(t) and wc <= 12 and dur < 2500:
        return True
    return False


def _must_join_mid_name(prev: str, nxt: str) -> bool:
    p = str(prev or "").rstrip()
    n = str(nxt or "").lstrip()
    if not p or not n:
        return False
    if _MID_NAME_END_RE.search(p):
        return True
    if _contains_protected(p) and n and (n[0].islower() or n[0] in ",;:—–-"):
        return True
    return False


def merge_micro_slots(
    segments: list[str],
    timing_map: list[Any],
    *,
    min_ms: int = MIN_SLOT_MS,
) -> tuple[list[str], list[dict[str, int]], dict[str, Any]]:
    """Merge micro-slots / fragments / mid-name cuts into neighbors."""
    texts = [str(s or "").strip() for s in (segments or []) if str(s or "").strip()]
    if not texts:
        return [], [], {"merged": 0}

    n = min(len(texts), len(timing_map)) if timing_map else len(texts)
    units: list[tuple[str, int, int]] = []
    for i in range(n):
        s = _t_start(timing_map[i]) if timing_map and i < len(timing_map) else i * 1000
        e = _t_end(timing_map[i]) if timing_map and i < len(timing_map) else s + 1000
        if e <= s:
            e = s + max(min_ms, len(texts[i]) * 40)
        units.append((texts[i], s, e))

    out: list[tuple[str, int, int]] = []
    merged = 0
    i = 0
    while i < len(units):
        text, s, e = units[i]
        dur = e - s
        needs = is_micro_or_fragment(text, dur, min_ms=min_ms)
        if needs and i + 1 < len(units):
            ntext, _ns, ne = units[i + 1]
            joined = f"{text} {ntext}".strip()
            out.append((joined, s, ne))
            merged += 1
            i += 2
            continue
        if needs and out:
            ptext, ps, _pe = out[-1]
            out[-1] = (f"{ptext} {text}".strip(), ps, e)
            merged += 1
            i += 1
            continue
        out.append((text, s, e))
        i += 1

    out, cont_merged = _merge_false_boundaries(out)
    merged += cont_merged

    new_texts = [t for t, _, _ in out]
    new_timing = [{"start": s, "end": e} for _, s, e in out]
    report = {
        "merged": merged,
        "continuation_merged": cont_merged,
        "before": len(units),
        "after": len(out),
        "min_ms": min_ms,
        "boundaries_changed": len(out) != len(units) or merged > 0,
    }
    return new_texts, new_timing, report


def _merge_false_boundaries(
    units: list[tuple[str, int, int]],
    *,
    max_combined_ms: int = 28000,
) -> tuple[list[tuple[str, int, int]], int]:
    """Merge Jr./молодший./lowercase false Whisper cuts."""
    if len(units) < 2:
        return units, 0
    try:
        from engines.smart_segmentation import would_break_forbidden
    except Exception:
        would_break_forbidden = None  # type: ignore[assignment]

    total_merged = 0
    cur = list(units)
    for _ in range(4):
        out: list[tuple[str, int, int]] = []
        merged = 0
        i = 0
        while i < len(cur):
            text, s, e = cur[i]
            if i + 1 < len(cur):
                ntext, _ns, ne = cur[i + 1]
                must = False
                reason = ""
                if would_break_forbidden is not None:
                    must, reason = would_break_forbidden(text, ntext)
                if not must and _must_join_mid_name(text, ntext):
                    must, reason = True, "mid_name_uk_en"
                combined = ne - s
                if must and combined <= max_combined_ms:
                    out.append((f"{text} {ntext}".strip(), s, ne))
                    merged += 1
                    logger.info(
                        "[SegmentNormalizer] join false boundary (%s)",
                        reason,
                    )
                    i += 2
                    continue
            out.append((text, s, e))
            i += 1
        total_merged += merged
        cur = out
        if merged == 0:
            break
    return cur, total_merged


def normalize_segments(
    segments: list[str],
    timing_map: list[Any],
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    min_ms: int = MIN_SLOT_MS,
    run_smart_split: bool = True,
) -> tuple[list[str], list[dict[str, int]], dict[str, Any]]:
    """
    Full Segment Normalizer:
    1) merge micro-slots / fragments / mid-name
    2) Adaptive / Smart segmentation for long monologues (optional)
    """
    from engines.pipeline_integrity.v2_gates import (
        segment_normalizer_enabled,
        smart_segmentation_enabled,
    )

    if not segment_normalizer_enabled():
        tm = [
            {"start": _t_start(timing_map[i]), "end": _t_end(timing_map[i])}
            for i in range(min(len(segments), len(timing_map or [])))
        ]
        return list(segments), tm, {"enabled": False}

    texts, timing, micro_report = merge_micro_slots(
        segments, timing_map, min_ms=min_ms
    )
    report: dict[str, Any] = {
        "enabled": True,
        "micro": micro_report,
        "smart": None,
        "boundaries_changed": bool(micro_report.get("boundaries_changed")),
    }

    use_smart = run_smart_split and smart_segmentation_enabled()
    if use_smart and texts:
        try:
            from engines.adaptive_segmentation import adapt_source_segments

            result = adapt_source_segments(
                texts,
                timing,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                overrides={"enabled": True, "min_ms": max(min_ms, 4500)},
            )
            if result.changed and result.segments:
                texts = list(result.segments)
                timing = list(result.timing_map)
                report["boundaries_changed"] = True
            report["smart"] = result.report
        except Exception as exc:
            logger.warning("SegmentNormalizer smart pass skipped: %s", exc)
            report["smart_error"] = type(exc).__name__

    logger.info(
        "[SegmentNormalizer] %d→%d micro_merged=%s",
        micro_report.get("before"),
        len(texts),
        micro_report.get("merged"),
    )
    return texts, timing, report


def _seg_source_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("original")
        or seg.get("source_text")
        or seg.get("whisper_text")
        or seg.get("plain_text")
        or seg.get("translation_text")
        or seg.get("translated_text")
        or seg.get("text")
        or ""
    ).strip()


def _seg_timing(seg: dict[str, Any], timing_map: list[Any], idx: int) -> dict[str, int]:
    if idx < len(timing_map or []):
        item = timing_map[idx]
        if isinstance(item, dict):
            return {
                "start": int(item.get("start", 0)),
                "end": int(item.get("end", 0)),
            }
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            return {"start": int(item[0]), "end": int(item[1])}
    s = int(seg.get("start_ms") or 0)
    e = int(seg.get("end_ms") or (s + int(seg.get("slot_ms") or 0)))
    return {"start": s, "end": e}


def normalize_segments_data(
    segments_data: list[dict[str, Any]],
    timing_map: list[Any] | None = None,
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    min_ms: int = MIN_SLOT_MS,
    task_info: dict[str, Any] | None = None,
    force: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, int]], dict[str, Any]]:
    """Normalize segment rows; on boundary change mint NEW ids (PSA3)."""
    from engines.pipeline_integrity.v2_gates import segment_normalizer_enabled

    tm = list(timing_map or [])
    if not force and not segment_normalizer_enabled():
        return list(segments_data or []), tm, {"enabled": False, "noop": True}

    active = [
        s
        for s in (segments_data or [])
        if isinstance(s, dict) and s.get("merged_into") is None and not s.get("archived")
    ]
    if not active:
        return list(segments_data or []), tm, {"enabled": True, "merged": 0}

    texts = [_seg_source_text(s) for s in active]
    if not tm or len(tm) < len(active):
        tm = [_seg_timing(active[i], tm, i) for i in range(len(active))]

    new_texts, new_timing, report = normalize_segments(
        texts,
        tm,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        min_ms=min_ms,
        run_smart_split=False,  # PSA4: micro/fragment/mid-name only pre-TTS
    )
    report["enabled"] = True

    if not report.get("boundaries_changed") and len(new_texts) == len(active):
        # Stamp slot_ms from timing; keep ids
        for i, seg in enumerate(active):
            if i < len(new_timing):
                s, e = new_timing[i]["start"], new_timing[i]["end"]
                seg["start_ms"] = s
                seg["end_ms"] = e
                seg["slot_ms"] = max(0, e - s)
        return list(segments_data or []), new_timing, report

    # Boundary change → archive old + NEW ids (PSA3), never edit-in-place
    from engines.pipeline_integrity.immutable_segment import (
        resegment_archive_and_reissue,
    )
    from engines.pipeline_integrity.identity_guard import bind

    archived, fresh, uuid_map = resegment_archive_and_reissue(
        active,
        new_texts,
        new_timing,
        stage="segment_normalizer",
        task_info=task_info,
        force=True,  # PSA4: reissue whenever normalizer changes boundaries
    )
    # Preserve translated text when 1:1 and content equal; else keep merged source
    for i, seg in enumerate(fresh):
        if i < len(new_timing):
            seg["start_ms"] = new_timing[i]["start"]
            seg["end_ms"] = new_timing[i]["end"]
            seg["slot_ms"] = max(
                0, new_timing[i]["end"] - new_timing[i]["start"]
            )
        try:
            bind(seg, text=_seg_source_text(seg), stage="segment_normalizer", allow_rebind=True, force=True)
        except Exception:
            pass

    report["archived"] = len(archived)
    report["new_ids"] = [str(s.get("segment_id") or "") for s in fresh]
    report["uuid_map"] = dict(uuid_map)
    report["reissued"] = True

    logger.info(
        "[SegmentNormalizer] reissue archived=%d new=%d",
        len(archived),
        len(fresh),
    )
    return fresh, new_timing, report
