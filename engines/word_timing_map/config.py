"""Word Timing Map configuration and sync modes."""

from __future__ import annotations

import os

MIN_WORD_GAP_MS = int(os.getenv("VM_WTM_MIN_WORD_GAP_MS", "80"))

# Phase 0/1: always collect maps (real or approximate).
COLLECTION_ALWAYS = True

# legacy | hybrid | word_timing — Phase 0/1: all modes = identical dub output.
SYNC_MODES = ("legacy", "hybrid", "word_timing")


def sync_mode() -> str:
    val = os.getenv("VM_WTM_SYNC_MODE", "legacy").strip().lower()
    return val if val in SYNC_MODES else "legacy"


def whisper_word_timestamps_enabled() -> bool:
    """Request real per-word timestamps from Whisper (not approximate)."""
    val = os.getenv("VM_WORD_TIMING_MAP", "0").strip().lower()
    return val not in ("0", "false", "no", "off", "disabled")


def is_enabled() -> bool:
    """Backward compat alias for whisper_word_timestamps_enabled."""
    return whisper_word_timestamps_enabled()


def collection_enabled() -> bool:
    """Word maps are always built in Phase 0/1 (real or estimated)."""
    return COLLECTION_ALWAYS


def optimizer_enabled() -> bool:
    """Phase 3+: Word Timing Optimizer. Off until Phase 3 is explicitly enabled."""
    if sync_mode() == "legacy":
        return False
    val = os.getenv("VM_WTM_OPTIMIZER", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def optimizer_auto_apply() -> bool:
    """Phase 4: automatically change text. Phase 3 = assistant/diagnostics only."""
    if not optimizer_enabled():
        return False
    val = os.getenv("VM_WTM_AUTO_APPLY", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def sso_fallback_enabled() -> bool:
    """Hybrid: SSO v2 after WTM failure. Legacy: SSO as today. word_timing: no SSO."""
    mode = sync_mode()
    if mode == "legacy":
        return True
    if mode == "word_timing":
        return False
    val = os.getenv("VM_WTM_FALLBACK_SSO", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def current_phase_label() -> str:
    if optimizer_auto_apply():
        return "phase4"
    if optimizer_enabled():
        return "phase3"
    return "phase0"  # includes phase1 persist-only
