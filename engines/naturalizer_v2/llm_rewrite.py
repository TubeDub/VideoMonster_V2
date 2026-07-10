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

    system = (
        "Ты профессиональный переводчик и литературный редактор.\n\n"
        "Перед тобой машинный перевод сегмента дубляжа.\n\n"
        "Твоя задача — полностью переписать текст так, чтобы носитель языка "
        f"никогда не догадался, что это машинный перевод.\n\n"
        f"Целевой язык: {lang_name}.\n"
        "Смысл должен быть сохранён на 100%.\n"
        "Разрешается менять любые слова, порядок слов, объединять или разбивать части.\n"
        "Запрещено менять факты, имена, даты, цифры и смысл.\n"
        "Имена людей (George Lucas, George Jr.) никогда не заменяй названиями франшиз, "
        "фильмов или брендов (Star Wars, Fiat и т.д.) — это разные сущности.\n"
        "Если в оригинале есть George Lucas — в переводе должно быть «Джордж Лукас», "
        "а не «Зоряні війни».\n"
        "Не добавляй новые факты. Не удаляй факты из оригинала.\n"
        "Исправь кальки, смешение языков, буквальный перевод, неестественный порядок слов.\n"
        "Имена, бренды и известные названия сохраняй в правильной форме.\n"
        "Ответ — только одна реплика для озвучки, без кавычек и пояснений."
    )

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
            temperature=0.4,
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
