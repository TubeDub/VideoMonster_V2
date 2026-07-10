"""LLM polish via llm_gateway — preserve meaning and length."""

from __future__ import annotations

import logging
import re

from engines.ai_core.grammar_agent.rule_engine import apply_style_pass
from engines.translation_naturalizer import _lang_name
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.grammar_agent.llm")


def _build_prompt(source: str, timing_text: str, tgt_lang: str) -> str:
    lang_name = _lang_name(normalize_lang(tgt_lang))
    ref_len = len(str(timing_text or "").strip())
    return (
        f"Polish grammar and punctuation in {lang_name}. "
        f"Preserve ALL facts, names, dates, numbers. "
        f"Keep length within ±10% ({ref_len} chars). "
        f"Do not add or remove information. "
        f"Source: {source.strip()} "
        f"Text: {timing_text.strip()} "
        f"Polished {lang_name}:"
    )


def _clean_llm_output(text: str) -> str:
    out = str(text or "").strip()
    out = re.sub(r'^["«»\']+|["«»\']+$', "", out)
    return out.strip()


def llm_polish(
    source: str,
    timing_text: str,
    *,
    tgt_lang: str = "ru",
    timeout: float | None = None,
) -> tuple[str | None, bool]:
    """
    LLM grammar polish. Returns (text, llm_used).
    Falls back to rule style pass on timeout/error/unavailability.
    """
    from engines.ai_core import llm_gateway

    raw = str(timing_text or "").strip()
    if not raw:
        return raw, False

    if not llm_gateway.is_available():
        return apply_style_pass(raw, tgt_lang=tgt_lang), False

    prompt = _build_prompt(source, raw, tgt_lang)
    try:
        from engines.translation_adapt import agent_llm_timeout

        llm_gateway.set_context(stage="grammar_polish")
        _timeout = timeout if timeout is not None else agent_llm_timeout(25.0)
        result = llm_gateway.chat(
            prompt,
            system=(
                "You are a professional dubbing grammar editor. "
                "Fix punctuation and grammar while preserving every fact, name, date, and number. "
                "Keep similar length (±10%). Return only the polished line."
            ),
            max_tokens=512,
            temperature=0.15,
            timeout=_timeout,
        )
    except Exception as exc:
        logger.debug("LLM grammar polish failed: %s", exc)
        result = None

    if result and str(result).strip():
        return _clean_llm_output(result), True

    return apply_style_pass(raw, tgt_lang=tgt_lang), False
