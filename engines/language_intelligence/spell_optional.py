"""Optional Windows spell hints — never required."""

from __future__ import annotations


def spell_hints(text: str, lang: str = "uk") -> list[str]:
    """
    Optional orthography hints from OS if available.
    Returns empty list when unavailable — module works identically without Windows.
    """
    _ = (text, lang)
    return []
