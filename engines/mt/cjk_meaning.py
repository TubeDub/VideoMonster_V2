"""Backward-compatible re-exports — see engines.mt.cross_script_guard. """

from __future__ import annotations

from engines.mt.cross_script_guard import (  # noqa: F401
    cjk_char_count,
    is_cjk_heavy,
    meaning_collapse,
    meaning_collapse_zh_to_cyrillic,
    source_script_leak,
)

__all__ = [
    "cjk_char_count",
    "is_cjk_heavy",
    "meaning_collapse",
    "meaning_collapse_zh_to_cyrillic",
    "source_script_leak",
]
