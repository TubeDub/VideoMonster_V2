# -*- coding: utf-8 -*-
"""Stage 24: UK TTS lock + snapshot census sync + soft-pad + ripple 80ms."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_force_uk_tts_identity_bans_cs_sk_pl_ru():
    from engines.tts_lang_lock import force_uk_tts_identity

    for bad in ("cs-CZ-AntoninNeural", "sk-SK-LukasNeural", "pl-PL-MarekNeural", "ru-RU-DmitryNeural"):
        ident = force_uk_tts_identity(target_lang="uk", engine_id="edge-offline", voice=bad)
        assert ident["language"] == "uk"
        assert ident["voice"].startswith("uk-UA-")
        assert not ident["voice"].startswith(("cs-", "sk-", "pl-", "ru-"))

    mk = force_uk_tts_identity(target_lang="uk", engine_id="tts_uk", voice="whatever")
    assert mk["voice"] == "mykyta"
    assert mk["engine_id"] == "tts_uk"


def test_uk_backend_override_piper_to_tts_uk_when_available(monkeypatch):
    """Stage 25 §1.1: for target=uk, piper MUST NEVER stay as default when
    tts_uk is installed. Voice must be a Mykyta family member, not
    ``uk_UA-oleksa-high``.
    """
    from engines import tts_lang_lock

    # Simulate tts_uk installed.
    monkeypatch.setattr(tts_lang_lock, "_TTS_UK_AVAILABLE_CACHE", {"ok": True})
    ident = tts_lang_lock.force_uk_tts_identity(
        target_lang="uk", engine_id="piper", voice="uk_UA-oleksa-high"
    )
    assert ident["engine_id"] == "tts_uk"
    assert ident["voice"] in ("mykyta", "tetiana", "lada")
    assert ident.get("tts_uk_available") is True

    # Simulate tts_uk NOT installed → fall back to Edge uk-UA-* (never piper/oleksa).
    monkeypatch.setattr(tts_lang_lock, "_TTS_UK_AVAILABLE_CACHE", {"ok": False})
    ident2 = tts_lang_lock.force_uk_tts_identity(
        target_lang="uk", engine_id="piper", voice="uk_UA-oleksa-high"
    )
    assert ident2["engine_id"] == "edge-offline"
    assert ident2["voice"].startswith("uk-UA-")
    assert ident2["voice"] != "uk_UA-oleksa-high"


def test_bind_pipeline_tts_from_info_uk_overrides_piper(monkeypatch):
    """Stage 25 §1.1: pipeline binder must override piper→tts_uk for target=uk."""
    from engines import tts_backends, tts_lang_lock

    monkeypatch.setattr(tts_lang_lock, "_TTS_UK_AVAILABLE_CACHE", {"ok": True})
    info = {
        "tts_engine": "piper",
        "voice": "uk_UA-oleksa-high",
        "target_lang": "uk",
    }
    eid = tts_backends.bind_pipeline_tts_from_info(info)
    assert eid == "tts_uk"
    assert info["tts_engine"] == "tts_uk"
    assert info["voice"] in ("mykyta", "tetiana", "lada")
    assert info["tts_backend"] == "tts_uk"
    assert info["tts_language"] == "uk"


def test_resolve_uk_tts_returns_tuple(monkeypatch):
    """Stage 25 §1.1 wrapper — single entry point returning (backend, voice)."""
    from engines import tts_lang_lock

    monkeypatch.setattr(tts_lang_lock, "_TTS_UK_AVAILABLE_CACHE", {"ok": True})
    backend, voice = tts_lang_lock.resolve_uk_tts("uk", "piper", None)
    assert backend == "tts_uk"
    assert voice == "mykyta"

    backend2, voice2 = tts_lang_lock.resolve_uk_tts("uk-UA", "edge-offline", "cs-CZ-AntoninNeural")
    assert backend2 == "edge-offline"
    assert voice2.startswith("uk-UA-")

    # Non-uk targets are pass-through.
    backend3, voice3 = tts_lang_lock.resolve_uk_tts("ru", "piper", "uk_UA-oleksa-high")
    assert backend3 == "piper"
    assert voice3 == "uk_UA-oleksa-high"


def test_latin_heavy_warning_threshold():
    from engines.tts_lang_lock import is_latin_heavy

    heavy, ratio = is_latin_heavy("Hello world this is latin", threshold=0.30)
    assert heavy is True
    assert ratio > 0.30
    ok, ratio2 = is_latin_heavy("Привіт світе, як справи сьогодні?", threshold=0.30)
    assert ok is False
    assert ratio2 < 0.30


def test_census_uses_repaired_snapshot_not_stale_live(tmp_path):
    """Root cause of audio_present=0 / padded_count=0: census on wrong list."""
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block
    from pydub import AudioSegment

    pad = tmp_path / "pad_silence_x.wav"
    AudioSegment.silent(duration=1200).export(str(pad), format="wav")

    live = [
        {
            "index": 0,
            "text": "Привіт",
            "file": None,
            "tts_ms": 0,
            "needs_re_tts": True,
        }
    ]
    snapshot = copy.deepcopy(live)
    snapshot[0]["file"] = str(pad.resolve())
    snapshot[0]["tts_ms"] = 1200
    snapshot[0]["audio_padded"] = True
    snapshot[0]["needs_re_tts"] = False

    info = {
        "session_dir": str(tmp_path),
        "segments_data": live,  # stale
        "padded_count": 1,
        "padded_indices": [0],
        "target_lang": "uk",
    }
    stale = _build_openddf_tts_pipeline_block(info)
    assert stale["audio_present"] == 0

    fixed = _build_openddf_tts_pipeline_block(info, segments_data=snapshot)
    assert fixed["audio_present"] == 1
    assert fixed["audio_missing"] == 0
    assert fixed["padded_count"] >= 1
    assert fixed["final_status"] == "ok_with_pads"


def test_build_timed_dub_track_syncs_snapshot_into_task_info(tmp_path, monkeypatch):
    from api import auto_dub_api as api
    from pydub import AudioSegment

    monkeypatch.setattr(api, "_artifacts_dir", lambda *_a, **_k: tmp_path)

    good = tmp_path / "ok.wav"
    AudioSegment.silent(duration=800).export(str(good), format="wav")

    segs = [
        {
            "index": 0,
            "segment_id": "okseg",
            "text": "Перший.",
            "final_tts_text": "Перший.",
            "file": str(good),
            "tts_ms": 800,
            "slot_ms": 1000,
            "start_ms": 0,
            "end_ms": 1000,
            "tts_language": "uk",
            "tts_voice": "mykyta",
            "tts_backend": "tts_uk",
        },
        {
            "index": 1,
            "segment_id": "holeseg",
            "text": "Другий без аудіо.",
            "final_tts_text": "Другий без аудіо.",
            "file": None,
            "tts_ms": 0,
            "needs_re_tts": True,
            "slot_ms": 1500,
            "start_ms": 1000,
            "end_ms": 2500,
        },
    ]
    # Live copy stays broken (simulates deepcopy divergence).
    live = copy.deepcopy(segs)
    live[1]["file"] = None
    timing = [{"start": 0, "end": 1000}, {"start": 1000, "end": 2500}]
    task_info = {
        "session_dir": str(tmp_path),
        "voice": "mykyta",
        "tts_engine": "tts_uk",
        "target_lang": "uk",
        "target_duration_ms": 5000,
        "video_duration_ms": 5000,
        "segments_data": live,
    }
    fake_task = {"info": task_info}

    def _fail_regen(*_a, **_k):
        return (None, 0)

    with patch.object(api, "AUTO_TASKS", {"t24": fake_task}):
        with patch.object(api, "_regen_segment_tts", side_effect=_fail_regen):
            with patch.object(api, "_commit_tts_group_result", return_value=None):
                with patch.object(api, "_video_duration_ms", return_value=5000):
                    timed, warnings, _report = api._build_timed_dub_track(
                        segs,
                        timing,
                        5000,
                        "t24",
                        style_params={},
                    )

    assert timed is not None
    assert "EXPORT_BLOCKED_MISSING_AUDIO" not in (warnings or [])
    # Synced into task_info — census must see pads.
    assert task_info.get("segments_data") is not None
    assert segs[1].get("audio_padded") or segs[1].get("silence_pad")
    assert int(task_info.get("padded_count") or 0) >= 1
    tp = task_info.get("tts_pipeline") or {}
    assert int(tp.get("audio_missing") or 0) == 0
    assert int(tp.get("audio_present") or 0) >= 1
    assert tp.get("final_status") in ("ok", "ok_with_pads")


def test_ripple_80ms_clears_small_overlaps():
    from engines.conflict_resolver import STAGE23_RIPPLE_OVERLAP_MS, ripple_shift_segment_dicts

    assert STAGE23_RIPPLE_OVERLAP_MS == 80
    segs = [
        {"index": 0, "start_ms": 0, "final_tts_duration_ms": 1000},
        {"index": 1, "start_ms": 900, "final_tts_duration_ms": 800},  # 100ms overlap
    ]
    stats = ripple_shift_segment_dicts(segs)
    assert stats["ripple_shifted"] >= 1
    assert segs[1]["start_ms"] >= 1000
    assert int(stats.get("overlap_count") or 0) == 0


def test_cleanup_keeps_pad_silence(tmp_path):
    from engines.pipeline_cleanup import cleanup_after_dub_complete

    session = tmp_path / "session"
    session.mkdir()
    pad = session / "pad_silence_abc.wav"
    pad.write_bytes(b"x" * 2000)
    tts = session / "tts_abc.wav"
    tts.write_bytes(b"y" * 2000)
    out = tmp_path / "out"
    out.mkdir()
    (out / "final.mp4").write_bytes(b"z" * 100)
    cleanup_after_dub_complete(out, session, keep_names={"final.mp4"})
    assert pad.is_file()
    assert tts.is_file()
    assert session.is_dir()
