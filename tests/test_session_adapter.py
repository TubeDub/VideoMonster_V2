"""Tests for SessionContextAdapter and session-scoped paths."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


@pytest.fixture
def output_root(tmp_path):
    d = tmp_path / "output"
    d.mkdir()
    return d


def test_activate_session_context_sets_artifacts_dir(output_root):
    from engines.dubbing_engine.project_session import create_session
    from engines.dubbing_engine.session_adapter import (
        activate_session_context,
        get_active_artifacts_dir,
    )

    sid = uuid.uuid4().hex
    session = create_session(sid, output_root, "movie")
    assert get_active_artifacts_dir(output_root) == output_root

    with activate_session_context(session) as ctx:
        assert ctx is not None
        assert get_active_artifacts_dir(output_root) == session.session_dir
        p = ctx.path("test.mp3")
        p.write_bytes(b"x")
        assert p.is_file()

    assert get_active_artifacts_dir(output_root) == output_root


def test_resolve_session_audio_prefers_session_dir(output_root):
    from engines.dubbing_engine.project_session import create_session
    from engines.dubbing_engine.session_adapter import resolve_session_audio

    sid = uuid.uuid4().hex
    session = create_session(sid, output_root, "movie")
    f = session.session_path("seg0001.mp3")
    f.write_bytes(b"mp3")

    resolved = resolve_session_audio(
        "seg0001.mp3",
        task_info={"session_dir": str(session.session_dir)},
        default_dir=output_root,
    )
    assert resolved == f


def test_resolve_session_audio_output_dir_when_session_missing(output_root):
    from engines.dubbing_engine.session_adapter import resolve_session_audio

    flat = output_root / "abc_seg0002.mp3"
    flat.write_bytes(b"mp3")

    resolved = resolve_session_audio(
        "abc_seg0002.mp3",
        task_info={"session_dir": str(output_root / "no_such_dir")},
        default_dir=output_root,
    )
    assert resolved == flat
    assert resolved.is_file()


def test_session_logger_format(output_root):
    from engines.dubbing_engine.project_session import create_session

    session = create_session(uuid.uuid4().hex, output_root, "movie")
    msg, _ = session.log.process("hello", {})
    assert msg.startswith("[Session ")
    assert "[ProjectSession]" in msg
