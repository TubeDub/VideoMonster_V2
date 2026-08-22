# -*- coding: utf-8 -*-
"""Single spoken-text authority for Simple: Review text == Edge-TTS input.

``final_tts_text`` is the only string that may be voiced after text-fit.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.tts_text_authority")

_WS = re.compile(r"\s+")


def text_hash(text: str) -> str:
    norm = _WS.sub(" ", str(text or "").strip())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def stamp_final_tts_text(
    seg: dict[str, Any],
    text: str,
    *,
    audit: dict[str, Any] | None = None,
    source: str = "text_fit",
) -> str:
    """Lock spoken text onto the segment (+ optional audit)."""
    final = _WS.sub(" ", str(text or "").strip())
    if not final:
        return ""
    try:
        from engines.translation_validation import stamp_authoritative_final_text

        stamp_authoritative_final_text(seg, final, audit=audit)
    except Exception:
        for key in (
            "text",
            "plain_text",
            "final_text",
            "tts_text",
            "text_for_tts",
            "voice_input",
            "translation_text",
        ):
            seg[key] = final
        if audit is not None:
            audit["final_text"] = final
            audit["tts_text"] = final
    seg["final_tts_text"] = final
    seg["spoken_text_source"] = "final_tts_text"
    seg["tts_text_hash"] = text_hash(final)
    seg["final_tts_source"] = str(source or "text_fit")
    if audit is not None:
        # Force Review/audit Final to the locked spoken text (do not leave a
        # longer stale semantic_text that populate would prefer later).
        audit["final_text"] = final
        audit["tts_text"] = final
        audit["final_tts_text"] = final
        audit["semantic_text"] = final
        audit["semantic_engine_text"] = final
        audit["tts_text_hash"] = seg["tts_text_hash"]
        audit["spoken_text_source"] = "final_tts_text"
    return final


def lock_segments_final_tts(
    segments_data: list[Any],
    texts: list[str],
    *,
    audits: list[Any] | None = None,
    source: str = "text_fit",
) -> list[str]:
    """Stamp final_tts_text on every active segment; return locked texts."""
    locked: list[str] = []
    audit_by = {}
    if audits:
        for a in audits:
            if isinstance(a, dict):
                audit_by[int(a.get("index", -1))] = a
    for i, raw in enumerate(texts):
        text = _WS.sub(" ", str(raw or "").strip())
        locked.append(text)
        if i >= len(segments_data) or not isinstance(segments_data[i], dict):
            continue
        if segments_data[i].get("merged_into") is not None:
            continue
        stamp_final_tts_text(
            segments_data[i],
            text,
            audit=audit_by.get(i),
            source=source,
        )
    return locked


def resolve_final_tts_text(seg: dict[str, Any] | None) -> str:
    """Prefer locked final_tts_text, then authoritative final fields."""
    if not isinstance(seg, dict):
        return ""
    locked = str(seg.get("final_tts_text") or "").strip()
    if locked:
        return locked
    try:
        from engines.translation_validation import resolve_final_text

        return str(resolve_final_text(seg) or "").strip()
    except Exception:
        return str(
            seg.get("final_text")
            or seg.get("tts_text")
            or seg.get("text")
            or ""
        ).strip()


def resolve_group_spoken_text(group: dict[str, Any]) -> str:
    """Text Edge-TTS must speak for a TTS group (plain, not SSML)."""
    for key in ("final_tts_text", "plain_text", "text"):
        val = str(group.get(key) or "").strip()
        if not val:
            continue
        if val.lstrip().startswith("<speak"):
            val = re.sub(r"<[^>]+>", " ", val).strip()
            val = _WS.sub(" ", val)
        if val:
            return val
    return ""


def prefer_locked_uk_spoken_text(
    text: str,
    *,
    group: dict[str, Any] | None = None,
    seg: dict[str, Any] | None = None,
) -> str:
    """Prefer recovered Final over stale voice_input / text_for_tts (zip 8fadb9dd)."""
    spoken = _WS.sub(" ", str(text or "").strip())
    locked = ""
    if isinstance(seg, dict):
        locked = str(seg.get("final_tts_text") or seg.get("plain_text") or "").strip()
    if not locked and isinstance(group, dict):
        locked = str(group.get("final_tts_text") or "").strip()
    try:
        from engines.text_slot_fit import prepare_uk_spoken_text
        from engines.tts_lang_lock import is_uk_tts_text_ok, rewrite_russian_leak_for_uk

        if locked:
            locked = prepare_uk_spoken_text(locked)
        spoken = prepare_uk_spoken_text(spoken)
        if locked and is_uk_tts_text_ok(locked):
            return locked
        candidate = spoken or locked
        if candidate and not is_uk_tts_text_ok(candidate):
            rewritten = prepare_uk_spoken_text(rewrite_russian_leak_for_uk(candidate))
            if rewritten and is_uk_tts_text_ok(rewritten):
                return rewritten
        if locked:
            return locked
    except Exception:
        pass
    return spoken or locked



def assert_tts_matches_final(
    spoken: str,
    expected: str,
    *,
    index: int = -1,
    task_id: str | None = None,
) -> bool:
    """Log warning when TTS buffer differs from locked final_tts_text."""
    a = _WS.sub(" ", str(spoken or "").strip())
    b = _WS.sub(" ", str(expected or "").strip())
    if a == b:
        return True
    logger.warning(
        "TTS text mismatch idx=%s task=%s spoken_hash=%s expected_hash=%s "
        "spoken=%r expected=%r",
        index,
        task_id,
        text_hash(a),
        text_hash(b),
        a[:120],
        b[:120],
    )
    return False
