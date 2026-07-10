"""LLM shorten/expand preserving meaning via llm_gateway."""

from __future__ import annotations

import logging
import re

from engines.ai_core.timing_agent.rule_rewrite import expand_rule, shorten_rule
from engines.translation_naturalizer import _lang_name
from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.timing_agent.llm")


def _clean_output(text: str) -> str:
    out = str(text or "").strip()
    out = re.sub(r'^["«»\']+|["«»\']+$', "", out)
    if "..." in out:
        return ""
    return out.strip()


def _build_shorten_prompt(text: str, slot_ms: int, tgt_lang: str) -> str:
    lang_name = _lang_name(normalize_lang(tgt_lang))
    target_ms = max(int(slot_ms or 0), 500)
    return (
        f"Shorten this {lang_name} dubbing line to fit ~{target_ms}ms of speech. "
        f"Preserve ALL facts, names, dates, numbers. "
        f"No ellipsis, no word cutting, no empty output. "
        f"Line: {text.strip()}"
    )


def _build_expand_prompt(text: str, slot_ms: int, tgt_lang: str) -> str:
    lang_name = _lang_name(normalize_lang(tgt_lang))
    target_ms = max(int(slot_ms or 0), 500)
    return (
        f"Slightly expand this {lang_name} dubbing line to better fill ~{target_ms}ms. "
        f"Use natural connectors only — no repeated words, no filler spam. "
        f"Preserve meaning. Line: {text.strip()}"
    )


def llm_shorten(
    text: str,
    *,
    slot_ms: int,
    tgt_lang: str = "ru",
    prev_context: str | None = None,
    timeout: float | None = None,
) -> tuple[str | None, bool]:
    from engines.ai_core import llm_gateway

    if not llm_gateway.is_available():
        return shorten_rule(text, tgt_lang=tgt_lang, prev_context=prev_context), False

    prompt = _build_shorten_prompt(text, slot_ms, tgt_lang)
    try:
        from engines.translation_adapt import agent_llm_timeout, llm_budget_status

        task_id = str(llm_budget_status().get("task") or "")
        llm_gateway.set_context(stage="timing_shorten")
        _timeout = timeout if timeout is not None else agent_llm_timeout(20.0)
        result = llm_gateway.chat(
            prompt,
            task_id=task_id,
            system=(
                "Professional dubbing editor. Shorten naturally for timing. "
                "Never truncate mid-word or use '...'. Return only the line."
            ),
            max_tokens=256,
            temperature=0.15,
            timeout=_timeout,
        )
    except Exception as exc:
        logger.debug("LLM shorten failed: %s", exc)
        result = None

    cleaned = _clean_output(result or "")
    if cleaned:
        try:
            from engines.pipeline_language_gate import is_critical_language_mismatch

            bad, _ = is_critical_language_mismatch(cleaned, target_lang=tgt_lang)
            if bad:
                cleaned = ""
        except Exception:
            pass
    if cleaned:
        return cleaned, True
    return shorten_rule(text, tgt_lang=tgt_lang, prev_context=prev_context), False


def llm_expand(
    text: str,
    *,
    slot_ms: int,
    tgt_lang: str = "ru",
    timeout: float | None = None,
) -> tuple[str | None, bool]:
    from engines.ai_core import llm_gateway

    if not llm_gateway.is_available():
        return expand_rule(text, tgt_lang=tgt_lang), False

    prompt = _build_expand_prompt(text, slot_ms, tgt_lang)
    try:
        from engines.translation_adapt import agent_llm_timeout, llm_budget_status

        task_id = str(llm_budget_status().get("task") or "")
        llm_gateway.set_context(stage="timing_expand")
        _timeout = timeout if timeout is not None else agent_llm_timeout(20.0)
        result = llm_gateway.chat(
            prompt,
            task_id=task_id,
            system=(
                "Professional dubbing editor. Expand slightly for pacing. "
                "No word repetition to fill time. Return only the line."
            ),
            max_tokens=256,
            temperature=0.2,
            timeout=_timeout,
        )
    except Exception as exc:
        logger.debug("LLM expand failed: %s", exc)
        result = None

    cleaned = _clean_output(result or "")
    if cleaned:
        try:
            from engines.pipeline_language_gate import is_critical_language_mismatch

            bad, _ = is_critical_language_mismatch(cleaned, target_lang=tgt_lang)
            if bad:
                cleaned = ""
        except Exception:
            pass
    if cleaned:
        return cleaned, True
    return expand_rule(text, tgt_lang=tgt_lang), False
