# -*- coding: utf-8 -*-
"""Stage 35 — diagnostic 8fadb9dd: TTS skip TypeError + stale RU parallel.

Zip metrics this suite locks:
- OpenDDF stage=TTS TypeError ``NoneType / float`` in TTSFailureReport.timestamp_iso
- Parallel TTS: PIPELINE_LANG_MIX cyrillic_ratio < 0.55 on
  «Эй, мужик, ты отлично справляешься. Ось долар. Долар? Ти серйозно?»
- PRE_TTS recovery healed idx 0 to UK Final; text_for_tts / voice_input stayed RU
- audio_present=0 audio_missing=32 padded_count=0 tts_ms_zero=32
  (31 cache misses discarded because skip handler crashed)
- voice planned uk_UA-mykyta-high; mix/mux never ran
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ZIP_IDX0_RU = (
    "Эй, мужик, ты отлично справляешься. Ось долар. Долар? Ти серйозно?"
)
ZIP_IDX0_UK = (
    "Гей, чувак, ти чудово справляєшся. Ось долар. Долар? Ти серйозно?"
)
ZIP_IDX16_RU = "Я даже не люблю об этом говорить, тому що я молод. Мені лише 25."
ZIP_FAILURE = {
    "error_message": (
        "PIPELINE_LANG_MIX: cyrillic_ratio < 0.55 parallel TTS "
        f"text={ZIP_IDX0_RU!r}"
    )
}


def test_sparse_tts_failure_dict_does_not_typeerror():
    from engines.dubbing_engine.tts_failure_diag import TTSFailureReport

    report = TTSFailureReport.from_partial_dict(ZIP_FAILURE)
    payload = report.to_dict()
    assert "timestamp" in payload
    assert payload["error_message"].startswith("PIPELINE_LANG_MIX")
    assert payload["timestamp_ms"] is not None
    iso = report.timestamp_iso
    assert "T" in iso


def test_from_partial_explicit_none_timestamp():
    from engines.dubbing_engine.tts_failure_diag import TTSFailureReport

    report = TTSFailureReport.from_partial_dict(
        {"error_message": "x", "timestamp_ms": None, "duration_ms": None}
    )
    assert report.to_dict()["timestamp_ms"]


def test_mark_tts_segment_skipped_sparse_dict(tmp_path):
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task
    from api.auto_dub_api import _mark_tts_segment_skipped

    task_id = "8fadb9dd-skip"
    init_auto_task(task_id, {"status": "running", "info": {"session_dir": str(tmp_path)}})
    segs = [{"segment_id": "s0", "index": 0, "text": ZIP_IDX0_RU, "file": "a.mp3"}]
    _mark_tts_segment_skipped(task_id, segs, [0], ZIP_FAILURE, reason="tts_failure")
    assert segs[0]["tts_status"] == "failed"
    assert segs[0]["file"] is None
    with STATE_LOCK:
        failures = AUTO_TASKS[task_id]["info"]["tts_failures"]
    assert failures
    assert failures[0].get("skipped_continue") is True


def test_prefer_locked_uk_over_stale_voice_input():
    from engines.tts_text_authority import prefer_locked_uk_spoken_text

    group = {"text": ZIP_IDX0_RU, "final_tts_text": ZIP_IDX0_UK, "plain_text": ZIP_IDX0_UK}
    seg = {
        "final_tts_text": ZIP_IDX0_UK,
        "text_for_tts": ZIP_IDX0_RU,
        "voice_input": ZIP_IDX0_RU,
    }
    out = prefer_locked_uk_spoken_text(ZIP_IDX0_RU, group=group, seg=seg)
    assert out == ZIP_IDX0_UK
    assert "Эй" not in out
    assert "мужик" not in out


def test_rewrite_zip_idx0_and_idx16():
    from engines.tts_lang_lock import (
        is_uk_tts_text_ok,
        rewrite_russian_leak_for_uk,
        uk_text_has_russian_leak,
    )

    assert uk_text_has_russian_leak(ZIP_IDX0_RU)
    assert not is_uk_tts_text_ok(ZIP_IDX0_RU)
    out0 = rewrite_russian_leak_for_uk(ZIP_IDX0_RU)
    assert "Эй" not in out0
    assert "мужик" not in out0
    assert "ты" not in out0
    assert is_uk_tts_text_ok(out0), out0

    assert uk_text_has_russian_leak(ZIP_IDX16_RU)
    out16 = rewrite_russian_leak_for_uk(ZIP_IDX16_RU)
    assert "даже" not in out16
    assert "говорить" not in out16
    assert is_uk_tts_text_ok(out16), out16


def test_recovery_stamps_voice_input_and_text_for_tts():
    from engines.language_validation.recovery import _stamp_text

    seg = {"text": ZIP_IDX0_RU}
    _stamp_text(seg, ZIP_IDX0_UK)
    assert seg["text_for_tts"] == ZIP_IDX0_UK
    assert seg["voice_input"] == ZIP_IDX0_UK
    assert seg["final_tts_text"] == ZIP_IDX0_UK
    assert seg["approved_text"] == ZIP_IDX0_UK


def test_parallel_rewrites_idx0_instead_of_pipeline_lang_mix(monkeypatch, tmp_path):
    import engines.tts_parallel as tp

    spoken: dict[str, str] = {}

    class FakeComm:
        def __init__(self, **kwargs):
            spoken["text"] = str(kwargs.get("text") or "")

        async def save(self, path):
            Path(path).write_bytes(b"x" * 400)

    fake_edge = MagicMock()
    fake_edge.Communicate = FakeComm
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)

    dest = tmp_path / "g0000.mp3"
    tp._synthesize_one_edge(ZIP_IDX0_RU, "uk-UA-OstapNeural", dest)
    assert dest.is_file()
    assert "Эй" not in spoken.get("text", "")
    assert "PIPELINE_LANG_MIX" not in spoken.get("text", "")
    assert "чувак" in spoken.get("text", "") or "Гей" in spoken.get("text", "")


def test_parallel_czech_still_raises_pipeline_lang_mix(tmp_path):
    import pytest
    import engines.tts_parallel as tp

    dest = tmp_path / "cz.mp3"
    with pytest.raises(RuntimeError, match="PIPELINE_LANG_MIX"):
        tp._synthesize_one_edge("Vítejme u další epizody.", "uk-UA-OstapNeural", dest)


def test_lang_lock_sync_clears_group_final_on_skip():
    from engines.tts_lang_lock import enforce_segments_lang_lock

    segs = [
        {
            "final_tts_text": "Эй, мой здоровяк.",
            "plain_text": "Эй, мой здоровяк.",
        }
    ]
    from unittest.mock import patch

    with patch(
        "engines.tts_lang_lock.force_remt_segment_no_cache", return_value=""
    ), patch(
        "engines.tts_lang_lock.rewrite_russian_leak_for_uk",
        return_value="Эй, мой здоровяк.",
    ):
        enforce_segments_lang_lock(
            segs,
            target_lang="uk",
            source_lang="en",
            simple_mode=True,
            fail_loud=True,
            app_dir=ROOT,
        )
    assert segs[0].get("skip_tts") is True
    assert segs[0].get("tts_skip_reason") == "russian_in_uk"
