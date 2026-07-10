"""Canonical language code normalization for TubeDub engines."""

from __future__ import annotations

from engines.mt.lang_codes import LANG_ALIASES


def normalize_lang(code: str | None, *, default: str = "en") -> str:
    """
    Normalize a language code to a short ISO-style tag.

    Uses LANG_ALIASES from engines.mt.lang_codes (eng→en, rus→ru, …).
    Chinese variants (zh-cn, zh_tw) collapse to ``zh``.
    """
    if code is None or not str(code).strip():
        return default
    c = str(code).strip().lower()
    if c.startswith("zh"):
        return "zh"
    return LANG_ALIASES.get(c, c.split("-")[0])
