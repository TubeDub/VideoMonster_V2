"""Backward-compatible export — real online engines live in online_engines.py."""

from __future__ import annotations

from engines.tts_engines.online_engines import online_engines


def stub_engines() -> list:
    """Historical name kept for registry imports; returns wired online engines."""
    return online_engines()
