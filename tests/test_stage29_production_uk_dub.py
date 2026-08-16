# -*- coding: utf-8 -*-
"""Stage 29 — production EN→UK dub gaps closed after Stage 28.

Covers remaining holes that Stage 28 path/pad work did not fully seal:
- assert-gate writes under session_dir/closed_loop/<task_id>/
- soft-pad fills skip_tts / tts_blocked timeline holes (audio_missing==0)
- UK synthesize refuses non-Cyrillic text (no Edge voicing of Latin/cs)
- UK Simple stamps ~4/7/12s + medium aggressiveness
- TTS cache never serves forbidden-locale voices for lang=uk
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _wav(path: Path, ms: int = 500, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


# --------------------------------------------------------------------------
# §B1 — assert work_root uses session_dir (not bare OUTPUT_DIR)
# --------------------------------------------------------------------------


def test_assert_audio_ready_writes_into_session_closed_loop(tmp_path, monkeypatch):
    from api import auto_dub_api as ada

    session_dir = tmp_path / "session"
    tid = "stage29assert"
    session_dir.mkdir(parents=True)

    # Force regenerate path: speakable text, no file on disk.
    seg = {
        "segment_id": "s0",
        "index": 0,
        "text": "Привіт усім друзям сьогодні",
        "final_tts_text": "Привіт усім друзям сьогодні",
        "start_ms": 0,
        "end_ms": 1500,
        "slot_ms": 1500,
        "tts_ms": 0,
        "file": None,
        "voice": "mykyta",
    }
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
        "tts_engine": "tts_uk",
        "mykyta_rate": 0.97,
        "mykyta_length_scale": 1.05,
    }

    def _fake_regen(*_a, **_k):
        # Simulate failed re-TTS so assert falls through to silence pad.
        return None, 0

    monkeypatch.setattr(ada, "_regen_segment_tts", _fake_regen)

    gate = ada._assert_segments_audio_ready(
        [seg],
        task_id=tid,
        task_info=task_info,
        voice="mykyta",
        allow_repair=True,
    )
    assert gate.get("ok") is True or int(gate.get("padded") or 0) >= 0
    pad = Path(str(seg.get("resolved_path") or seg.get("file") or ""))
    assert pad.is_file(), seg
    assert pad.parent == (session_dir / "closed_loop" / tid).resolve()
    assert seg.get("audio_padded") or seg.get("silence_pad")


# --------------------------------------------------------------------------
# §B3/B4 — soft-pad blocked / skip_tts holes
# --------------------------------------------------------------------------


def test_soft_pad_fills_skip_tts_timeline_hole(tmp_path):
    from api.auto_dub_api import _soft_pad_missing_segments

    session_dir = tmp_path / "session"
    tid = "blocked29"
    seg = {
        "segment_id": "blocked",
        "index": 0,
        "text": "Hello world",
        "final_tts_text": "Hello world",
        "start_ms": 0,
        "end_ms": 1000,
        "slot_ms": 1000,
        "tts_ms": 0,
        "file": None,
        "skip_tts": True,
        "tts_blocked": True,
        "tts_skip_reason": "reject_non_target_lang_mix",
    }
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
    }
    stats = _soft_pad_missing_segments(
        [seg], task_info=task_info, task_id=tid, timing_map=None
    )
    assert stats["padded_count"] == 1
    assert Path(seg["resolved_path"]).is_file()
    assert task_info["final_status"] == "ok_with_pads"


# --------------------------------------------------------------------------
# §A3 — synthesize refuses non-Cyrillic for target=uk (no Edge voicing)
# --------------------------------------------------------------------------


def test_synthesize_refuses_non_cyrillic_uk_text(tmp_path):
    from engines import tts_backends
    from engines.tts_engines import registry

    called = {"n": 0}

    def _fake(*_a, **_k):
        called["n"] += 1
        raise AssertionError("backend must not be called for non-Cyrillic UK text")

    real = registry.synthesize
    registry.synthesize = _fake
    try:
        result = tts_backends.synthesize_with_backend(
            "Hello this is English only speech",
            "mykyta",
            str(tmp_path / "bad.wav"),
            engine_id="tts_uk",
            target_lang="uk",
        )
    finally:
        registry.synthesize = real

    assert result.ok is False
    assert called["n"] == 0
    assert "cyrillic" in str(result.error or "").lower()


def test_synthesize_allows_uk_cyrillic_text(tmp_path):
    from engines import tts_backends
    from engines.tts_engines import registry

    class _OK:
        ok = True
        engine_id = "tts_uk"
        error = None
        meta: dict = {"tts_backend": "tts_uk", "voice": "mykyta"}

    def _fake(text, voice, path, **kw):
        _wav(Path(path), 300)
        return _OK()

    real = registry.synthesize
    registry.synthesize = _fake
    try:
        result = tts_backends.synthesize_with_backend(
            "Привіт, це український текст для озвучення",
            "mykyta",
            str(tmp_path / "ok.wav"),
            engine_id="tts_uk",
            target_lang="uk",
        )
    finally:
        registry.synthesize = real

    assert result.ok is True


# --------------------------------------------------------------------------
# §A4 — cache miss on forbidden voice for lang=uk
# --------------------------------------------------------------------------


def test_tts_cache_miss_forbidden_voice_for_uk(tmp_path):
    from engines.tts_cache import lookup_tts_cache, store_tts_cache

    src = _wav(tmp_path / "src.wav", 400)
    # Even if somehow stored under cs-CZ, lookup with lang=uk must miss.
    store_tts_cache(
        src,
        "Текст",
        "cs-CZ-VlastaNeural",
        engine_id="edge-offline",
        lang="uk",
        cache_dir=tmp_path / "cache",
    )
    hit = lookup_tts_cache(
        "Текст",
        "cs-CZ-VlastaNeural",
        engine_id="edge-offline",
        lang="uk",
        cache_dir=tmp_path / "cache",
        ext=".wav",
    )
    assert hit is None


def test_tts_cache_key_uk_differs_from_non_uk_lang():
    from engines.tts_cache import tts_cache_key

    uk = tts_cache_key("Привіт", "mykyta", engine_id="tts_uk", lang="uk")
    ru = tts_cache_key("Привіт", "mykyta", engine_id="tts_uk", lang="ru")
    assert uk != ru


# --------------------------------------------------------------------------
# §D — UK Simple defaults include 4/7/12 + medium
# --------------------------------------------------------------------------


def test_simple_pipeline_uk_segment_defaults_4_7_12():
    from engines.simple_dub_pipeline import apply_simple_pipeline_policy

    info = {"target_lang": "uk", "user_mode": "basic"}
    apply_simple_pipeline_policy(info, user_mode="basic")
    assert info["segment_min_ms"] == 4000
    assert info["segment_preferred_ms"] == 7000
    assert info["segment_max_ms"] == 12000
    assert info["segmentation_aggressiveness"] == 0.50
    assert info["aggressiveness"] == "medium"
    assert info["mykyta_rate"] == 0.97
    assert info["max_atempo"] <= 1.05 + 1e-6


def test_happy_path_glue_honours_uk_4s_floor():
    from engines.segment_merger import merge_stt_segments_happy_path

    # Three short phrases that only glue if min_safe is ~4s (not requiring 5s+).
    texts = ["One.", "Two.", "Three."]
    timing = [
        {"start": 0, "end": 1400},
        {"start": 1500, "end": 2800},
        {"start": 2900, "end": 4200},
    ]
    merged, mt = merge_stt_segments_happy_path(
        texts, timing, min_safe_ms=4000, max_span_ms=12000
    )
    assert len(merged) == 1
    span = int(mt[0]["end"]) - int(mt[0]["start"])
    assert 4000 <= span <= 12000
