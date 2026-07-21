"""Re-translate only segments marked after adaptive resegment (TZ §11.3)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.adaptive_segmentation.retranslate")


def retranslate_segment_text(
    source_text: str,
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
) -> str:
    """Best-effort single-segment MT without touching Translation Agent core."""
    src = str(source_text or "").strip()
    if not src:
        return ""
    s = (src_lang or "en").split("-")[0]
    t = (tgt_lang or "uk").split("-")[0]
    try:
        from engines.translation import translate_text

        return str(translate_text(src, s, t) or "").strip()
    except Exception as exc:
        logger.debug("translate_text unavailable: %s", exc)
    try:
        from engines.translation_compat import translate_text as compat_translate

        return str(compat_translate(src, target=t, source=s) or "").strip()
    except Exception as exc:
        logger.debug("translation_compat unavailable: %s", exc)
    return ""


def apply_retranslate_if_needed(
    seg: dict[str, Any],
    source_text: str,
    *,
    src_lang: str,
    tgt_lang: str,
) -> bool:
    """Fill empty/marked half after split. Returns True if text updated."""
    if not seg.get("needs_retranslate") and str(
        seg.get("plain_text") or seg.get("text") or ""
    ).strip():
        return False
    new_text = retranslate_segment_text(
        source_text, src_lang=src_lang, tgt_lang=tgt_lang
    )
    if not new_text:
        return False
    seg["plain_text"] = new_text
    seg["text"] = new_text
    seg["final_text"] = new_text
    seg["translation_text"] = new_text
    seg["needs_retranslate"] = False
    stages = list(seg.get("adaptation_stages") or [])
    if "adaptive_retranslate" not in stages:
        stages.append("adaptive_retranslate")
    seg["adaptation_stages"] = stages
    return True
