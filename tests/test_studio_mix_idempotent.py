"""P0 studio mix idempotency and TTS asset retention tests."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_task_state():
    from engines import dub_task_state as dts

    with dts.STATE_LOCK:
        dts.AUTO_TASKS.clear()
        dts.AUTO_TASK_CONTROLS.clear()
    yield
    with dts.STATE_LOCK:
        dts.AUTO_TASKS.clear()
        dts.AUTO_TASK_CONTROLS.clear()


def test_mix_returns_existing_output_when_done(tmp_path, monkeypatch):
    """POST /api/studio/mix must return 200 with existing MP4 when task is already done."""
    import api.studio_api as studio_mod
    from engines.dub_task_state import AUTO_TASKS, init_auto_task

    task_id = uuid.uuid4().hex
    output_name = "clip_OUTPUT_abcd1234.mp4"
    output_path = tmp_path / output_name
    output_path.write_bytes(b"FAKE_MP4")

    monkeypatch.setattr(studio_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(studio_mod, "_studio_access", lambda sid=None: None)

    init_auto_task(
        task_id,
        {
            "status": "done",
            "output_file": output_name,
            "info": {"output_path_full": str(output_path), "keep_studio_assets": True},
        },
    )

    render_called = {"count": 0}

    def _should_not_render(*_args, **_kwargs):
        render_called["count"] += 1
        return None, ["unexpected render"]

    monkeypatch.setattr(studio_mod, "_render_studio_timed_audio", _should_not_render)

    from app import app as flask_app

    with flask_app.test_request_context(
        f"/api/studio/mix/{task_id}",
        method="POST",
        data=json.dumps({}),
        content_type="application/json",
    ):
        resp, status = studio_mod.api_studio_mix(task_id)
        data = json.loads(resp.get_data(as_text=True))

    assert status == 200
    assert data["ok"] is True
    assert data["output_file"] == output_name
    assert data.get("already_mixed") is True
    assert render_called["count"] == 0


def test_auto_mix_does_not_cleanup_session(tmp_path, monkeypatch):
    """_mark_studio_mix_done keeps session_dir and TTS when keep_assets=True (default)."""
    import api.studio_api as studio_mod
    from engines import dub_task_state as dts
    from engines.dub_task_state import AUTO_TASKS

    task_id = uuid.uuid4().hex
    session_dir = tmp_path / "sessions" / task_id
    session_dir.mkdir(parents=True)
    tts_file = session_dir / "e092654d_g0000.mp3"
    tts_file.write_bytes(b"mp3")
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"mp4")

    monkeypatch.setattr(studio_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path)

    with dts.STATE_LOCK:
        AUTO_TASKS[task_id] = {
            "status": "studio_ready",
            "info": {
                "session_dir": str(session_dir),
                "keep_studio_assets": True,
                "segments_data": [{"index": 0, "file": tts_file.name}],
                "tts_files": [tts_file.name],
                "mux_base_id": "e092654d",
            },
        }

    state = {
        "session_id": task_id,
        "segments": [{"index": 0, "text": "hi", "file": tts_file.name, "start_ms": 0, "end_ms": 1000}],
    }

    studio_mod._mark_studio_mix_done(
        task_id,
        timed_audio_path=str(tmp_path / "timed.mp3"),
        final_output=str(output_path),
        state=state,
    )

    assert tts_file.is_file(), "TTS must remain after auto-mix"
    assert session_dir.is_dir(), "session_dir must remain after auto-mix"
    with dts.STATE_LOCK:
        info = AUTO_TASKS[task_id]["info"]
    assert info.get("keep_studio_assets") is True


def test_resolve_session_audio_output_dir_fallback(tmp_path):
    """Regenerated TTS in flat output/ is found via segments_data when old name is stale."""
    from engines.dubbing_engine.session_adapter import resolve_session_audio

    regen = tmp_path / "1a8b0e4d_seg0000.mp3"
    regen.write_bytes(b"mp3")

    resolved = resolve_session_audio(
        "e092654d_g0001.mp3",
        task_info={
            "session_dir": str(tmp_path / "missing_session"),
            "segments_data": [{"index": 1, "file": "1a8b0e4d_seg0000.mp3"}],
        },
        default_dir=tmp_path,
        segment_index=1,
    )
    assert resolved == regen
    assert resolved.is_file()


def test_resolve_session_audio_prefers_exact_output_filename(tmp_path):
    """Exact filename in output/ wins over index glob fallback."""
    from engines.dubbing_engine.session_adapter import resolve_session_audio

    exact = tmp_path / "1a8b0e4d_seg0000.mp3"
    exact.write_bytes(b"exact")
    other = tmp_path / "deadbeef_seg0000.mp3"
    other.write_bytes(b"other")

    resolved = resolve_session_audio(
        "1a8b0e4d_seg0000.mp3",
        task_info={"session_dir": str(tmp_path / "gone")},
        default_dir=tmp_path,
        segment_index=0,
    )
    assert resolved == exact
