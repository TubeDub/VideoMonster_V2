# -*- coding: utf-8 -*-
"""Align Translation Review Final with spoken TTS text.

Last-resort gates before Review UI and before synthesis:
1. Split shared MT blobs across neighbouring slots (debleed).
2. Deflate phrase loops.
3. Force tts_text / text_for_tts / plain_text = Final (+ terminal punct).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.tts_review_align")

_AUDIT_TEXT_KEYS = (
    "raw_translation",
    "naturalized_text",
    "final_text",
    "tts_text",
    "semantic_text",
    "semantic_engine_text",
    "quality_pass_before",
    "quality_pass_after",
)

_SEG_TEXT_KEYS = (
    "text",
    "plain_text",
    "final_text",
    "tts_text",
    "text_for_tts",
    "voice_input",
    "translation_text",
    "semantic_text",
    "semantic_engine_text",
    "grammar_text",
    "timing_text",
    "approved_text",
)


def _norm_space(s: str) -> str:
    return " ".join(str(s or "").split()).strip()


def _restore_terminal(text: str, *, original: str = "", reference: str = "") -> str:
    out = _norm_space(text)
    if not out:
        return out
    ref = _norm_space(reference) or _norm_space(original)
    try:
        from engines.semantic_meaning import restore_terminal_close

        out = restore_terminal_close(out, original=original or ref)
    except Exception:
        pass
    # If Final ends with sentence punct and TTS lost it, restore from Final.
    if ref and ref[-1:] in ".!?…" and out[-1:] not in ".!?…":
        out = out + ref[-1]
    return out


def _audit_by_index(audits: list) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for a in audits or []:
        if isinstance(a, dict):
            out[int(a.get("index", -1))] = a
    return out


def debleed_audit_fields(
    audits: list,
    source_segments: list[str],
    *,
    keys: tuple[str, ...] = _AUDIT_TEXT_KEYS,
) -> dict[str, Any]:
    """Run adjacent-blob debleed on parallel audit text columns."""
    from engines.translation_naturalizer import debleed_adjacent_batch_copies

    by_idx = _audit_by_index(audits)
    if not by_idx:
        return {"changed": 0, "pairs": []}
    n = max(by_idx.keys()) + 1
    sources = [str(s or "") for s in (source_segments or [])]
    while len(sources) < n:
        sources.append("")

    changed = 0
    pairs: list[tuple[int, int]] = []
    for key in keys:
        col = [_norm_space(by_idx[i].get(key) if i in by_idx else "") for i in range(n)]
        if not any(col):
            continue
        fixed = debleed_adjacent_batch_copies(sources, col)
        for i in range(n):
            if i not in by_idx:
                continue
            before = col[i]
            after = _norm_space(fixed[i] if i < len(fixed) else "")
            if after and after != before:
                by_idx[i][key] = after
                changed += 1
                if i + 1 < n and before and col[i + 1] == before:
                    pairs.append((i, i + 1))

    # Phrase-loop deflate on finals
    try:
        from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop

        for i, row in by_idx.items():
            for key in ("final_text", "tts_text", "naturalized_text", "raw_translation"):
                val = _norm_space(row.get(key))
                if val and has_phrase_loop(val, min_repeats=2):
                    healed = deflate_phrase_loop(val)
                    if (
                        healed
                        and healed != val
                        and not has_phrase_loop(healed, min_repeats=2)
                    ):
                        row[key] = healed
                        changed += 1
                        row["phrase_loop_healed"] = True
    except Exception:
        pass

    return {"changed": changed, "pairs": sorted(set(pairs))}


def sync_segments_from_audits(
    segments_data: list,
    audits: list,
    source_segments: list[str] | None = None,
) -> int:
    """Stamp segment TTS-bound fields from debleeded audit Final."""
    by_idx = _audit_by_index(audits)
    synced = 0
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("tts_blocked") or seg.get("skip_tts"):
            continue
        row = by_idx.get(i) or {}
        original = ""
        if source_segments and i < len(source_segments):
            original = str(source_segments[i] or "")
        final = _norm_space(
            row.get("final_text")
            or row.get("semantic_text")
            or row.get("naturalized_text")
            or seg.get("approved_text")
            or seg.get("final_text")
            or seg.get("text")
            or ""
        )
        if not final:
            continue
        # Hard-trim honesty: do not overwrite a shorter spoken prefix with a
        # longer Final (that recreates Review≠audio after trim_overlap).
        spoken = _norm_space(
            seg.get("spoken_fit_text")
            or (seg.get("timing_meta") or {}).get("spoken_fit_text")
            or row.get("tts_text")
            or seg.get("tts_text")
            or ""
        )
        voice_cut = bool(
            seg.get("voice_truncated")
            or row.get("voice_truncated")
            or (seg.get("timing_meta") or {}).get("speech_trimmed")
        )
        if voice_cut and spoken and len(spoken) + 8 < len(final):
            final = spoken
        final = _restore_terminal(
            final,
            original=original,
            reference=_norm_space(row.get("naturalized_text") or final),
        )
        for key in _SEG_TEXT_KEYS:
            if key == "approved_text" and not seg.get("approved_text"):
                continue
            if str(seg.get(key) or "").strip() != final:
                seg[key] = final
        # Force spoken fields to Final (or spoken prefix when voice was cut)
        seg["final_text"] = final
        seg["text"] = final
        seg["plain_text"] = final
        seg["tts_text"] = final
        seg["text_for_tts"] = final
        if row:
            row["final_text"] = final
            row["tts_text"] = final
        synced += 1
    return synced


def freeze_spoken_to_review_final(
    segments: list[str],
    segments_data: list,
    audits: list | None = None,
    source_segments: list[str] | None = None,
) -> list[str]:
    """Return segment strings forced to Review Final (post-approval TTS freeze)."""
    by_idx = _audit_by_index(audits or [])
    out: list[str] = []
    n = max(len(segments or []), len(segments_data or []), max(by_idx.keys(), default=-1) + 1)
    for i in range(n):
        seg = segments_data[i] if i < len(segments_data) and isinstance(segments_data[i], dict) else {}
        row = by_idx.get(i) or {}
        original = ""
        if source_segments and i < len(source_segments):
            original = str(source_segments[i] or "")
        if seg.get("tts_blocked") or seg.get("skip_tts") or row.get("tts_blocked"):
            out.append("")
            continue
        final = _norm_space(
            seg.get("approved_text")
            or row.get("final_text")
            or seg.get("final_text")
            or row.get("semantic_text")
            or seg.get("text")
            or (segments[i] if i < len(segments) else "")
            or ""
        )
        if final:
            final = _restore_terminal(
                final,
                original=original,
                reference=_norm_space(row.get("naturalized_text") or final),
            )
            if isinstance(seg, dict) and seg:
                for key in (
                    "text",
                    "plain_text",
                    "final_text",
                    "tts_text",
                    "text_for_tts",
                    "translation_text",
                ):
                    seg[key] = final
            if row:
                row["final_text"] = final
                row["tts_text"] = final
        out.append(final)
    return out


def _snap_stale_semantic_blobs(audits: list) -> int:
    """Force semantic_* to match debleeded Final when semantic is a shared MT blob."""
    try:
        from engines.translation_validation import is_shared_mt_blob_reclaim
    except Exception:
        return 0
    snapped = 0
    for row in audits or []:
        if not isinstance(row, dict):
            continue
        final = _norm_space(row.get("final_text"))
        if not final:
            continue
        for key in ("semantic_text", "semantic_engine_text"):
            sem = _norm_space(row.get(key))
            if sem and is_shared_mt_blob_reclaim(final, sem):
                row[key] = final
                snapped += 1
    return snapped


def align_info_for_translation_review(info: dict[str, Any]) -> dict[str, Any]:
    """Mutate task info audits/segments so Review Final == Text for TTS."""
    sources = list(info.get("source_segments") or [])
    audits = list(info.get("translation_audits") or [])
    report = debleed_audit_fields(audits, sources)
    semantic_snapped = _snap_stale_semantic_blobs(audits)
    sd = list(info.get("segments_data") or [])
    synced = sync_segments_from_audits(sd, audits, sources)
    # Also debleed the parallel segments list texts if present as strings later
    info["translation_audits"] = audits
    if sd:
        info["segments_data"] = sd
    info["review_align"] = {
        "debleed_changed": report.get("changed", 0),
        "debleed_pairs": report.get("pairs") or [],
        "synced_segments": synced,
        "semantic_snapped": semantic_snapped,
    }
    if report.get("changed") or synced or semantic_snapped:
        logger.info(
            "review_align: debleed_changed=%s pairs=%s synced=%s semantic_snapped=%s",
            report.get("changed"),
            report.get("pairs"),
            synced,
            semantic_snapped,
        )
    return info.get("review_align") or {}
