"""LLM full-sentence rewrite — editor-translator mode."""

from __future__ import annotations

import os
import re
from typing import Any

from engines.naturalizer_v2.config import is_v2_enabled


def _api_key() -> str | None:
    return (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
    )


def _lang_name(code: str) -> str:
    from engines.translation_naturalizer import _lang_name as ln

    return ln(code)


def _enabled() -> bool:
    return is_v2_enabled() and (os.getenv("VM_TRANSLATE_NATURAL", "1").strip().lower() not in ("0", "false", "no", "off"))


def rewrite_segment_llm(
    text: str,
    *,
    original: str = "",
    raw_mt: str = "",
    tgt_lang: str = "uk",
    src_lang: str | None = None,
    prev_context: str | None = None,
    problems: list[str] | None = None,
    preserved_entities: list[str] | None = None,
    force: bool = False,
    literary: bool = False,
) -> str | None:
    """
    Full sentence rewrite — professional translator/editor.
    Returns None if LLM unavailable or disabled.
    """
    if not _enabled() and not force:
        return None
    if not str(text or "").strip():
        return None
    # Availability is decided by AI Core (local OR cloud), not an OpenAI key.
    from engines.ai_core import llm_gateway

    if not llm_gateway.is_available():
        return None

    lang_name = _lang_name(tgt_lang)
    src_name = _lang_name(src_lang) if src_lang else "unknown"
    lang = (tgt_lang or "").split("-")[0].lower()
    literary = bool(literary) or lang == "uk"

    system = (
        "Ты профессиональный переводчик и литературный редактор дубляжа.\n\n"
        "Перед тобой машинный перевод сегмента. Полностью перепиши его так, "
        f"чтобы носитель языка ({lang_name}) не узнал машинный перевод.\n\n"
        f"Целевой язык: {lang_name}.\n"
        "Смысл, факты, имена, даты и цифры — 100% сохранить.\n"
        "Можно менять любые слова, падежи, порядок слов, объединять/разбивать фразы.\n"
        "Исправь кальки (could not help → не міг позбутися відчуття), "
        "ломаную морфологию (хлопчику … проїжджав → хлопець … проїжджав), "
        "буквальный MT и неестественный порядок слов.\n"
        "Имена людей (George Lucas, George Jr.) никогда не заменяй названиями франшиз "
        "(Star Wars / Зоряні війни) или брендов (Fiat) — это разные сущности.\n"
        "George Jr. → Джордж-молодший; USC оставляй как USC или "
        "«Університет Південної Каліфорнії»; Star Wars → «Зоряні війни».\n"
        "Ответ — только одна готовая реплика для озвучки, без кавычек и пояснений."
    )
    if literary and lang == "uk":
        from engines.naturalizer_v2.literary_uk import literary_prompt_extra

        system = system + literary_prompt_extra()

    user_parts: list[str] = [
        f"Source language: {src_name}",
        f"Target language: {lang_name}",
    ]
    if original.strip():
        user_parts.append(f"Original speech (Whisper):\n{original.strip()}")
    if raw_mt.strip():
        user_parts.append(f"Raw machine translation:\n{raw_mt.strip()}")
    if problems:
        user_parts.append(f"Detected problems: {', '.join(problems[:10])}")
    if preserved_entities:
        user_parts.append(
            "Preserve exactly (names/brands/titles): "
            + ", ".join(preserved_entities[:15])
        )
    if prev_context:
        user_parts.append(f"Previous segment (context only):\n{prev_context}")
    user_parts.append(f"Rewrite this dubbing line completely:\n{text.strip()}")

    try:
        # All LLM traffic goes through AI Core (single gateway): cached,
        # budgeted and logged. Anti-truncation is enforced inside the gateway.
        from engines.ai_core import llm_gateway

        content = llm_gateway.chat(
            "\n\n".join(user_parts),
            system=system,
            temperature=0.55 if literary else 0.4,
            max_tokens=400,
            timeout=60,
        )
        if not content:
            return None
        content = re.sub(r'^["\'«»]+|["\'«»]+$', "", content.strip()).strip()
        if not content:
            return None
        from engines.sentence_integrity import validate_tts_text

        ok, _iss = validate_tts_text(content)
        if not ok:
            return None
        return content
    except Exception:
        return None
