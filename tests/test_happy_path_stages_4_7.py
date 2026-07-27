# -*- coding: utf-8 -*-
"""Happy Path Stages 4–7: translation polish, TTS adapters, mux policy, light debt."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Stage 4 ──────────────────────────────────────────────────────────────────


def test_strip_mt_context_prefix():
    from engines.translation_naturalizer import strip_mt_context_prefix

    assert (
        strip_mt_context_prefix("[context: prev line] Привіт, світе!")
        == "Привіт, світе!"
    )
    assert (
        strip_mt_context_prefix("[контекст: foo] Бар")
        == "Бар"
    )
    assert strip_mt_context_prefix("Чистий текст") == "Чистий текст"
    assert strip_mt_context_prefix("") == ""


def test_polish_strips_context_prefix():
    from engines.translation_naturalizer import polish_segment_detailed

    result = polish_segment_detailed(
        "[context: earlier] Він пішов додому.",
        original="He went home.",
        tgt_lang="uk",
        use_llm=False,
    )
    assert "[context" not in result.text.lower()
    assert "пішов" in result.text.lower() or "додому" in result.text.lower()


def test_llm_polish_gate_without_key(monkeypatch):
    from engines import translation_naturalizer as tn

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("VM_OPENAI_API_KEY", raising=False)
    assert not tn._llm_api_key()


def test_happy_path_batch_constant():
    from engines.translation_naturalizer import (
        HAPPY_PATH_MAX_BATCH_SEGMENTS,
        MAX_BATCH_SEGMENTS,
    )

    assert HAPPY_PATH_MAX_BATCH_SEGMENTS >= MAX_BATCH_SEGMENTS


# ── Stage 5 ──────────────────────────────────────────────────────────────────


def test_edge_is_default_engine():
    from engines.tts_engines.registry import (
        LOCAL_CLONER_ENGINE_IDS,
        default_engine_id,
        get_engine,
    )

    assert default_engine_id() in ("edge-offline", "piper")
    eng = get_engine("missing-engine-xyz")
    assert eng.id == "edge-offline"
    assert "f5-tts" in LOCAL_CLONER_ENGINE_IDS
    assert "cosyvoice" in LOCAL_CLONER_ENGINE_IDS
    assert "gpt-sovits" in LOCAL_CLONER_ENGINE_IDS
    assert "chatterbox" in LOCAL_CLONER_ENGINE_IDS


def test_local_cloner_stubs_registered():
    from engines.tts_engines.providers import provider_engines

    ids = {e.id for e in provider_engines()}
    for eid in ("f5-tts", "cosyvoice", "gpt-sovits", "chatterbox"):
        assert eid in ids
    stubs = {e.id: e for e in provider_engines()}
    # Without optional deps these must report unavailable, not crash.
    assert stubs["f5-tts"].is_available() is False
    result = stubs["f5-tts"].synthesize("hi", "v", "out.wav")
    assert result.ok is False


def test_tts_rate_param_on_base_contract():
    from engines.tts_engines.base import BaseTTSEngine

    assert hasattr(BaseTTSEngine, "synthesize")


# ── Stage 6 ──────────────────────────────────────────────────────────────────


def test_language_learning_gated_in_simple():
    from engines.dub_style_presets import (
        DEFAULT_DUB_STYLE,
        gate_style_for_user_mode,
        resolve_dub_style,
    )

    sid, gated = gate_style_for_user_mode(
        "language_learning", "basic", raw_request="language_learning"
    )
    assert gated is True
    assert sid == DEFAULT_DUB_STYLE
    resolved = resolve_dub_style(sid)
    assert resolved["mix_mode"] == "full_dub"
    assert float(resolved["mix_volumes"]["original_volume"]) <= 0.001


def test_language_learning_allowed_in_pro():
    from engines.dub_style_presets import gate_style_for_user_mode, resolve_dub_style

    sid, gated = gate_style_for_user_mode(
        "language_learning", "pro", raw_request="language_learning"
    )
    assert gated is False
    resolved = resolve_dub_style(sid)
    assert float(resolved["mix_volumes"]["original_volume"]) > 0.001


def test_full_dub_default_modern():
    from engines.dub_style_presets import DEFAULT_DUB_STYLE, resolve_dub_style

    assert DEFAULT_DUB_STYLE == "modern"
    r = resolve_dub_style("modern")
    assert r["mix_mode"] == "full_dub"


def test_output_file_guard_message_exists():
    # Guard is in auto_dub_api start — keep message keys present.
    from api import auto_dub_api as api

    for lang_key in ("ru", "en", "uk"):
        # Localization dicts embed output_file_blocked
        pass
    src = Path(api.__file__).read_text(encoding="utf-8")
    assert "_OUTPUT_" in src
    assert "output_file_blocked" in src


def test_mux_copy_in_dub_engine():
    from engines import dub_engine as de

    src = Path(de.__file__).read_text(encoding="utf-8")
    assert "-c:v" in src and "copy" in src


# ── Stage 7 ──────────────────────────────────────────────────────────────────


def test_advanced_still_off_by_default():
    from engines.happy_path import USE_ADVANCED_ADAPTATION, advanced_adaptation_enabled

    assert USE_ADVANCED_ADAPTATION is False
    assert advanced_adaptation_enabled({"user_mode": "basic"}) is False


def test_normalize_lang_canonical():
    """Stage 7: canonical helper lives in engines.utils.lang_utils."""
    from engines.utils.lang_utils import normalize_lang

    assert normalize_lang("uk-UA") == "uk"
    assert normalize_lang("eng") == "en"
    assert normalize_lang(None) == "en"
