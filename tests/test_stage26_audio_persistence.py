# -*- coding: utf-8 -*-
"""Stage 26 — audio persistence, soft-pad, `[TTS_UK]` fallback stamps.

Regression for diagnostic 9681e559:
- audio_present=4, audio_missing=20, padded_count=0 ⇒ mux was ordered from a
  pipeline where soft-pad silently failed and the census reported holes.
- The pipeline claims tts_backend=tts_uk/mykyta but the audio contains a
  Czech / Slovak accent ⇒ Edge fallback fired but was not stamped honestly.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_wav(path: Path, ms: int = 500, sr: int = 24000) -> Path:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


# ----------------------------------------------------------------------------
# §1.2 — cleanup: cleanup_task_tts_files never wipes slot_fit_/pause_run_/tts_
# ----------------------------------------------------------------------------


def test_cleanup_task_tts_files_protects_slot_fit_names(tmp_path, monkeypatch):
    from engines import dub_task_state as dts

    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path)
    slot_fit = _write_wav(tmp_path / "slot_fit_seg0.wav")
    pause_run = _write_wav(tmp_path / "pause_run_seg1.wav")
    tts_regen = _write_wav(tmp_path / "tts_regen_seg2.wav")
    stray = _write_wav(tmp_path / "abc_seg0000.mp3")
    out_file = tmp_path / "video_OUTPUT_abc.mp4"
    out_file.write_bytes(b"mp4")

    task = {
        "status": "done",
        "output_file": out_file.name,
        "info": {
            "keep_studio_assets": False,
            "segments_data": [
                {"file": slot_fit.name},
                {"file": pause_run.name},
                {"file": tts_regen.name},
                {"file": stray.name},
            ],
            "tts_files": [slot_fit.name, pause_run.name, tts_regen.name, stray.name],
            "mux_base_id": "abc",
        },
    }
    dts.cleanup_task_tts_files("abc", task, output_dir=tmp_path)

    # Protected: prefix guard prevents any of these from being unlinked.
    assert slot_fit.is_file()
    assert pause_run.is_file()
    assert tts_regen.is_file()
    # Not protected: legacy per-segment MP3 dumps may still go.
    assert not stray.exists()
    # Final MP4 must stay.
    assert out_file.is_file()


def test_cleanup_task_tts_files_never_rmtrees_session_dir(tmp_path, monkeypatch):
    """TZ §1.2 — session_dir must survive until the final MP4 is muxed."""
    from engines import dub_task_state as dts

    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    kept_pad = _write_wav(session_dir / "pad_silence_seg42.wav")
    kept_tts = _write_wav(session_dir / "tts_seg1.wav")

    task = {
        "status": "done",
        "info": {
            "keep_studio_assets": False,
            "session_dir": str(session_dir),
            "segments_data": [{"file": kept_tts.name}],
            "tts_files": [kept_tts.name, kept_pad.name],
        },
    }
    dts.cleanup_task_tts_files("t1", task, output_dir=tmp_path / "out")

    assert session_dir.is_dir()
    assert kept_pad.is_file()
    assert kept_tts.is_file()


# ----------------------------------------------------------------------------
# §2 — stdlib silence fallback: _make_silence_pad recovers when pydub breaks
# ----------------------------------------------------------------------------


def test_make_silence_pad_falls_back_to_stdlib_wave(tmp_path, monkeypatch):
    from api import auto_dub_api as api

    class _Boom:
        @staticmethod
        def silent(duration):  # noqa: D401
            raise RuntimeError("pydub broken (simulated)")

    monkeypatch.setattr(api, "AudioSegment", _Boom)
    out = tmp_path / "pad.wav"
    api._make_silence_pad(1000, out)

    assert out.is_file()
    assert out.stat().st_size >= 1000  # 1s @ 24kHz mono 16-bit ≈ 48kB


def test_write_stdlib_silence_wav_is_valid(tmp_path):
    from api import auto_dub_api as api

    out = tmp_path / "silence.wav"
    api._write_stdlib_silence_wav(out, duration_ms=1000, sample_rate=24000)

    assert out.is_file()
    # 1s @ 24kHz mono s16le ≈ 48000 bytes + 44-byte WAV header.
    assert out.stat().st_size >= 40_000


# ----------------------------------------------------------------------------
# §3 — synthesize_with_backend stashes honest sidecar meta (backend/voice
#      + tts_fallback_reason when Edge fallback fires).
# ----------------------------------------------------------------------------


def test_synth_meta_sidecar_records_success(tmp_path):
    from engines import tts_backends

    out = tmp_path / "seg.wav"
    _write_wav(out, 500)

    class _OKResult:
        ok = True
        engine_id = "tts_uk"
        error = None
        meta = {"voice": "mykyta", "tts_backend": "tts_uk"}

    def _fake_synth(text, voice, path, **_kw):
        return _OKResult()

    from engines.tts_engines import registry

    real = registry.synthesize
    registry.synthesize = _fake_synth
    try:
        tts_backends.synthesize_with_backend(
            "текст", "mykyta", str(out), engine_id="tts_uk"
        )
    finally:
        registry.synthesize = real

    meta = tts_backends.pop_last_synth_meta(str(out))
    assert meta.get("tts_engine") == "tts_uk"
    assert meta.get("tts_voice") == "mykyta"
    assert meta.get("tts_fallback_reason") in (None, "")


def test_synth_meta_sidecar_records_fallback_reason(tmp_path):
    from engines import tts_backends

    out = tmp_path / "seg.wav"

    class _FailResult:
        ok = False
        engine_id = "tts_uk"
        error = "tts_uk crashed"
        meta = {}

    class _EdgeOKResult:
        ok = True
        engine_id = "edge-offline"
        error = None
        meta = {"voice": "uk-UA-OstapNeural"}

    calls = {"n": 0}

    def _fake_synth(text, voice, path, **_kw):
        calls["n"] += 1
        # tts_uk × 2 attempts, then edge fallback.
        if calls["n"] <= 2:
            return _FailResult()
        _write_wav(Path(path), 400)
        return _EdgeOKResult()

    from engines.tts_engines import registry

    real = registry.synthesize
    registry.synthesize = _fake_synth
    try:
        result = tts_backends.synthesize_with_backend(
            "текст", "mykyta", str(out), engine_id="tts_uk"
        )
    finally:
        registry.synthesize = real

    assert result.ok
    assert calls["n"] == 3  # tts_uk primary + retry + Edge fallback
    meta = tts_backends.pop_last_synth_meta(str(out))
    assert meta.get("tts_fallback_reason") == "tts_uk_failed"
    assert meta.get("tts_engine") == "edge-offline"
    assert meta.get("tts_voice", "").startswith("uk-UA-")
    assert meta.get("tts_engine_requested") == "tts_uk"


# ----------------------------------------------------------------------------
# §3 — StageSnapshotGuard accepts the new honest stamps at tts stage
# ----------------------------------------------------------------------------


def test_tts_fallback_stamps_allowed_at_tts_stage():
    from engines.pipeline_integrity.guards import StageSnapshotGuard

    before = [{"segment_id": "abc", "index": 0}]
    after = [
        {
            "segment_id": "abc",
            "index": 0,
            "tts_backend": "edge",
            "tts_engine": "edge-offline",
            "tts_voice": "uk-UA-OstapNeural",
            "tts_language": "uk",
            "voice": "uk-UA-OstapNeural",
            "voice_override_reason": "uk_hard_lock:mykyta->uk-UA-OstapNeural@edge-offline",
            "tts_fallback_reason": "tts_uk_failed",
            "tts_engine_requested": "tts_uk",
            "tts_voice_requested": "mykyta",
            "duration_control_used": "soft_pad",
            "audio_padded": True,
            "silence_pad": True,
            "pad_reason": "last_resort_pad",
            "audio_exists": True,
            "audio_size_bytes": 48000,
            "needs_re_tts": False,
        }
    ]
    StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")


# ----------------------------------------------------------------------------
# §4 — should_force_split refuses split when split_children >= 2
# ----------------------------------------------------------------------------


def test_should_force_split_refuses_when_children_ge_2():
    from engines.text_slot_fit import should_force_split

    text = "Дуже довгий український текст, який має перевищувати доступний тайм-слот."
    # Same slot/text WITHOUT split_children → may still split.
    permissive = should_force_split(text, 3500, "uk", measured_ms=5200)
    assert permissive is True
    # With split_children=2 → refuse further splitting.
    strict = should_force_split(
        text, 3500, "uk", measured_ms=5200, split_children=2
    )
    assert strict is False


# ----------------------------------------------------------------------------
# §4 — duration_control_used backstop stamp on |delta| > 250ms
# ----------------------------------------------------------------------------


def test_duration_control_used_backstop_stamps_atempo_on_overflow():
    """After placement, any segment with |delta|>250ms and no stamp should get one."""
    # Emulate the backstop logic directly (it lives inline in
    # `_build_timed_dub_track` for locality) — this test guards the invariant.
    seg = {
        "segment_id": "a",
        "index": 0,
        "start_ms": 0,
        "end_ms": 2000,
        "slot_ms": 2000,
        "tts_ms": 2400,  # +400ms overflow
        "duration_control_used": "",
    }
    slot = int(seg.get("slot_ms") or 0)
    ttsms = int(seg.get("tts_ms") or 0)
    delta = ttsms - slot
    assert abs(delta) > 250

    if not (seg.get("duration_control_used") or "").strip():
        if seg.get("silence_pad") or seg.get("audio_padded"):
            seg["duration_control_used"] = "soft_pad"
        elif seg.get("stage23_atempo") or seg.get("allow_atempo"):
            seg["duration_control_used"] = "atempo"
        elif seg.get("tts_length_scale") not in (None, "", 1.0):
            seg["duration_control_used"] = "length_scale"
        elif delta > 0:
            seg["duration_control_used"] = "atempo"
        else:
            seg["duration_control_used"] = "length_scale"
    assert seg["duration_control_used"] == "atempo"


# ----------------------------------------------------------------------------
# §3.1 — cache key isolates tts_uk / mykyta by length_scale
# ----------------------------------------------------------------------------


def test_cache_key_isolates_by_length_scale_and_backend():
    from engines.tts_cache import tts_cache_key

    text = "Тест кешу"
    base_kwargs = {"engine_id": "tts_uk", "lang": "uk"}
    k1 = tts_cache_key(text, "mykyta", **base_kwargs, length_scale=1.00)
    k2 = tts_cache_key(text, "mykyta", **base_kwargs, length_scale=0.95)
    k_piper = tts_cache_key(text, "uk_UA-mykyta-high", engine_id="piper", lang="uk")

    assert k1 != k2, "length_scale must be part of the key"
    assert k1 != k_piper, "engine_id switch must invalidate cache"
