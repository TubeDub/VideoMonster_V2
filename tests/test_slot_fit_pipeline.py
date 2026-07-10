"""Tests for pipeline slot-fit loop and i18n studio keys."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydub import AudioSegment

APP_DIR = Path(__file__).resolve().parent.parent


def test_pipeline_slot_fit_marks_green_when_fits(tmp_path, monkeypatch):
    from api.auto_dub_api import OUTPUT_DIR, _pipeline_slot_fit_segments

    monkeypatch.setattr("api.auto_dub_api.OUTPUT_DIR", tmp_path)

    mp3 = tmp_path / "seg0.mp3"
    AudioSegment.silent(duration=800).export(mp3, format="mp3")

    segments = [{"index": 0, "text": "Короткая фраза", "file": mp3.name}]
    timing = [{"start": 0, "end": 2000}]

    stats = _pipeline_slot_fit_segments(
        segments,
        timing,
        voice="ru-RU-DmitryNeural",
        target_lang="ru",
        source_segments=["Short"],
        task_id="test_slot_fit",
    )

    assert stats["total"] == 1
    assert stats["compressed"] == 1
    assert segments[0].get("fitted_file")
    assert segments[0]["container_status"] == "green"
    assert float(segments[0]["overflow_pct"]) <= 5.0


def test_pipeline_slot_fit_audio_only_overflow(tmp_path, monkeypatch):
    """slot_fit must not mutate segment text — audio-only overflow handling."""
    from api.auto_dub_api import OUTPUT_DIR, _pipeline_slot_fit_segments

    monkeypatch.setattr("api.auto_dub_api.OUTPUT_DIR", tmp_path)

    mp3 = tmp_path / "long.mp3"
    AudioSegment.silent(duration=2500).export(mp3, format="mp3")
    original_text = "Длинная фраза для теста"
    segments = [{"index": 0, "text": original_text, "file": mp3.name}]
    timing = [{"start": 0, "end": 2000}]

    stats = _pipeline_slot_fit_segments(
        segments,
        timing,
        voice="ru-RU-DmitryNeural",
        target_lang="ru",
        source_segments=["Long phrase"],
        max_attempts=3,
        task_id="test_audio_only",
    )

    assert stats["total"] == 1
    assert segments[0]["text"] == original_text
    tm = segments[0].get("timing_meta") or {}
    assert tm.get("slot_fit_compressed") is not True


def test_slot_fit_preserves_canonical_tts_file(tmp_path, monkeypatch):
    """slot_fit audio-only path must not delete canonical tts_file_path artifact."""
    from api.auto_dub_api import OUTPUT_DIR, _pipeline_slot_fit_segments

    monkeypatch.setattr("api.auto_dub_api.OUTPUT_DIR", tmp_path)

    canonical = "6160133d_g0012.mp3"
    mp3 = tmp_path / canonical
    AudioSegment.silent(duration=2500).export(mp3, format="mp3")
    segments = [
        {
            "index": 12,
            "segment_id": "42a5d3cda3e7471798b2d7e75cca3903",
            "text": "Длинная фраза для теста",
            "file": canonical,
            "tts_file_path": canonical,
        }
    ]
    timing = [{"start": 0, "end": 2000}]

    _pipeline_slot_fit_segments(
        segments,
        timing,
        voice="ru-RU-DmitryNeural",
        target_lang="ru",
        source_segments=["Long phrase"],
        max_attempts=3,
        task_id="test_preserve_canonical",
    )

    assert mp3.is_file(), "canonical tts_file_path artifact must survive slot_fit"
    assert segments[0]["file"] == canonical


def test_prepare_dub_segment_audio_caps_atempo_at_115(tmp_path, monkeypatch):
    from engines.timing_fit import DUB_MAX_ATEMPO, prepare_dub_segment_audio

    captured = {}

    def fake_atempo(in_path, tempo, out_path, *, max_atempo=1.18):
        captured["tempo"] = tempo
        captured["max_atempo"] = max_atempo
        AudioSegment.silent(duration=1800).export(out_path, format="wav")

    monkeypatch.setattr("engines.timing_fit._atempo", fake_atempo)

    src = tmp_path / "long.wav"
    AudioSegment.silent(duration=2400).export(src, format="wav")
    _, meta = prepare_dub_segment_audio(src, 2000, tmp_path / "work")

    assert meta["atempo"] <= DUB_MAX_ATEMPO
    assert captured.get("max_atempo") == DUB_MAX_ATEMPO
    assert captured.get("tempo", 1.0) <= DUB_MAX_ATEMPO


@pytest.mark.parametrize("lang_file", ["ru.json", "uk.json", "en.json"])
def test_studio_i18n_keys_present(lang_file):
    keys_required = [
        "studio.title",
        "studio.track.dub",
        "studio.plugin.loudness",
        "studio.plugin.compressor",
        "studio.btn_autoshorten",
        # Новые ключи пайплайна (studio_ready-flow)
        "studio.mix_project",
        "studio.mix_confirm",
        "studio.mixing",
        "studio.mix_done",
        "studio.overflow_modal.btn_regen_tts",
        "studio.overflow_modal.btn_keep",
        "dub.step_studio_preparing",
    ]
    data = json.loads((APP_DIR / "static" / "i18n" / lang_file).read_text(encoding="utf-8"))
    for key in keys_required:
        assert key in data, f"{key} missing in {lang_file}"


def test_locale_utils_defaults():
    from engines.locale_utils import resolve_server_locale

    assert resolve_server_locale("ru-RU") == "ru"
    assert resolve_server_locale("uk-UA") == "uk"
    assert resolve_server_locale("en-US") == "en"
    assert resolve_server_locale(None, "uk-UA,en;q=0.9") == "uk"


# ── Тесты нового пайплайна: пайплайн останавливается на studio_ready ──────────

def test_pipeline_stops_at_studio_ready_not_done(tmp_path, monkeypatch):
    """После TTS+slot_fit пайплайн должен завершиться со статусом studio_ready,
    а НЕ пытаться создать MP4."""
    import threading
    import uuid

    monkeypatch.setattr("api.auto_dub_api.OUTPUT_DIR", tmp_path)

    from api.auto_dub_api import AUTO_TASKS, AUTO_TASK_CONTROLS, STATE_LOCK

    task_id = uuid.uuid4().hex
    with STATE_LOCK:
        AUTO_TASKS[task_id] = {
            "status": "running",
            "step": "preparing",
            "progress": 0.0,
            "info": {},
            "errors": [],
        }
        AUTO_TASK_CONTROLS[task_id] = {"stop": False, "editing": False}

    # publish_studio_ready устанавливает status="studio_ready"
    # (и НЕ продолжает timing/DubEngine)
    from api.studio_api import publish_studio_ready

    url = publish_studio_ready(task_id)
    with STATE_LOCK:
        t = AUTO_TASKS[task_id]
    assert t["status"] == "studio_ready", (
        f"Ожидается studio_ready, получен: {t['status']}"
    )
    assert t["step"] == "studio"
    # Нет output_file — MP4 ещё не создан
    assert not t.get("output_file"), "pipeline не должен создавать MP4 до mix"


def test_studio_mix_endpoint_checks_overflow(tmp_path, monkeypatch):
    """POST /api/studio/mix/<task_id> должен вернуть 409, если есть красные сегменты
    и force не передан. Тест вызывает view-функцию через test_request_context."""
    import uuid
    import json as _json

    import api.studio_api as studio_mod
    from api.studio_api import _save_session

    task_id = uuid.uuid4().hex

    session = {
        "session_id": task_id,
        "task_id": task_id,
        "segments": [
            {
                "id": "0",
                "index": 0,
                "text": "Test",
                "start_ms": 0,
                "end_ms": 2000,
                "file": None,
                "overflow_pct": 40.0,
                "container_status": "red",
            }
        ],
        "timing_map": [{"start": 0, "end": 2000}],
        "duration_ms": 10000,
        "video_path": str(tmp_path / "fake.mp4"),
        "video_preview": None,
    }
    _save_session(session)

    # Отключаем guard-проверку (лицензию/фичи) — пусть endpoint работает свободно
    monkeypatch.setattr(studio_mod, "_studio_access", lambda sid=None: None)

    from app import app as flask_app  # type: ignore

    with flask_app.test_request_context(
        f"/api/studio/mix/{task_id}",
        method="POST",
        data=_json.dumps({}),
        content_type="application/json",
    ):
        resp, status = studio_mod.api_studio_mix(task_id)
        data = _json.loads(resp.get_data(as_text=True))

    assert status == 409, f"Ожидался 409 при красных сегментах, получен: {status}"
    assert data["error_code"] == "overflow_segments"
    assert data["overflow_count"] == 1


def test_studio_mix_endpoint_force_skips_overflow_check(tmp_path, monkeypatch):
    """С force=true смешивание должно начаться даже при красных сегментах."""
    import uuid
    import json as _json

    import api.studio_api as studio_mod
    from api.studio_api import _save_session

    task_id = uuid.uuid4().hex

    session = {
        "session_id": task_id,
        "task_id": task_id,
        "segments": [
            {
                "id": "0",
                "index": 0,
                "text": "Test",
                "start_ms": 0,
                "end_ms": 2000,
                "file": None,
                "overflow_pct": 40.0,
                "container_status": "red",
            }
        ],
        "timing_map": [{"start": 0, "end": 2000}],
        "duration_ms": 10000,
        "video_path": str(tmp_path / "fake.mp4"),
        "video_preview": None,
    }
    _save_session(session)

    # Отключаем лицензионный guard и мокаем FFmpeg-зависимые функции
    monkeypatch.setattr(studio_mod, "_studio_access", lambda sid=None: None)
    monkeypatch.setattr(
        studio_mod,
        "_render_studio_timed_audio",
        lambda tid, state: (str(tmp_path / "timed.mp3"), []),
    )
    monkeypatch.setattr(
        studio_mod,
        "_mix_studio_mp4_with_task_settings",
        lambda tid, tpath, state: (True, str(tmp_path / "output.mp4"), []),
    )
    (tmp_path / "output.mp4").write_bytes(b"FAKE")

    from app import app as flask_app  # type: ignore

    with flask_app.test_request_context(
        f"/api/studio/mix/{task_id}",
        method="POST",
        data=_json.dumps({"force": True}),
        content_type="application/json",
    ):
        result = studio_mod.api_studio_mix(task_id)
        if isinstance(result, tuple):
            resp, status = result
        else:
            resp, status = result, 200
        data = _json.loads(resp.get_data(as_text=True))

    assert status == 200, f"Ожидался 200 с force=true, получен {status}"
    assert data["ok"] is True
    assert data["output_file"] == "output.mp4"
