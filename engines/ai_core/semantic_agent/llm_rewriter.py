"""LLM natural rewrite via llm_gateway when available."""

from __future__ import annotations

import logging
import re

from engines.ai_core.semantic_agent.rule_engine import rule_rewrite
from engines.translation_naturalizer import _lang_name
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.semantic_agent.llm")


def _build_prompt(
    source: str,
    raw: str,
    tgt_lang: str,
    *,
    dialogue_block: str = "",
) -> str:
    lang_name = _lang_name(normalize_lang(tgt_lang))
    ctx = f"\nDialogue context:\n{dialogue_block}\n" if dialogue_block.strip() else ""
    return (
        f"Rewrite as natural spoken {lang_name} for dubbing. "
        f"Preserve meaning, intent, emotion, and ALL proper names/abbreviations. "
        f"Do NOT translate literally word-by-word. "
        f"Full sentence restructure is allowed when meaning stays intact.{ctx}"
        f"Original: {source.strip()} "
        f"Machine translation: {raw.strip()} "
        f"Natural dub line:"
    )


def _clean_llm_output(text: str) -> str:
    out = str(text or "").strip()
    out = re.sub(r'^["«»\']+|["«»\']+$', "", out)
    return out.strip()


def llm_rewrite(
    source: str,
    raw: str,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    dialogue_block: str = "",
    timeout: float | None = None,
) -> tuple[str | None, bool]:
    """
    LLM natural rewrite. Returns (text, llm_used).
    Falls back to rule_engine on timeout/error/unavailability.
    """
    from engines.ai_core import llm_gateway

    if not llm_gateway.is_available():
        fallback = rule_rewrite(
            raw,
            source=source,
            tgt_lang=tgt_lang,
            prev_context=prev_context,
        )
        return fallback, False

    prompt = _build_prompt(source, raw, tgt_lang, dialogue_block=dialogue_block)
    try:
        from engines.translation_adapt import agent_llm_timeout, llm_budget_status

        task_id = str(llm_budget_status().get("task") or "")
        llm_gateway.set_context(stage="semantic_rewrite")
        _timeout = timeout if timeout is not None else agent_llm_timeout(25.0)
        result = llm_gateway.chat(
            prompt,
            task_id=task_id,
            system=(
                "You are a professional dubbing editor. Rewrite the translation "
                "to sound natural while preserving every fact, name, date, and number. "
                "Return only the rewritten line, no quotes or explanation."
            ),
            max_tokens=512,
            temperature=0.2,
            timeout=_timeout,
        )
    except Exception as exc:
        logger.debug("LLM rewrite failed: %s", exc)
        result = None

    if result and str(result).strip():
        return _clean_llm_output(result), True

    fallback = rule_rewrite(
        raw,
        source=source,
        tgt_lang=tgt_lang,
        prev_context=prev_context,
    )
    return fallback, False


def llm_rewrite_variant(
    source: str,
    raw: str,
    *,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    variant: str = "LLM",
    timeout: float | None = None,
) -> tuple[str, bool]:
    """Generate one LLM candidate; returns (text, llm_used)."""
    text, used = llm_rewrite(
        source,
        raw,
        tgt_lang=tgt_lang,
        prev_context=prev_context,
        timeout=timeout,
    )
    return text or raw, used
