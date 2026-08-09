# -*- coding: utf-8 -*-
"""TZ: cleanup_after_dub_complete must never rmtree session or drop segment audio."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_cleanup_after_dub_keeps_session_and_audio(tmp_path):
    from engines import pipeline_cleanup as pc

    import ast

    src = Path(pc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
                base = func.value
                if isinstance(base, ast.Name) and base.id == "shutil" and name == "rmtree":
                    # Any rmtree whose first arg mentions session_dir is forbidden.
                    if node.args:
                        arg_src = ast.dump(node.args[0])
                        assert "session_dir" not in arg_src, arg_src
    body = src.split("def cleanup_after_dub_complete")[1].split("return removed")[0]
    assert "keep_segment_audio=False" not in body
    assert "keep_segment_audio=True" in body
    assert "slot_fit_*" not in pc.TEMP_GLOBS
    assert "pause_run_*" not in pc.TEMP_GLOBS
    assert "tts_*" not in pc.TEMP_GLOBS

    out = tmp_path / "output"
    out.mkdir()
    session = tmp_path / "sessions" / "t1"
    session.mkdir(parents=True)
    (session / "tts_000.mp3").write_bytes(b"x" * 1500)
    (session / "pause_run_000.wav").write_bytes(b"y" * 1500)
    (session / "slot_fit").mkdir()
    (session / "slot_fit" / "slot_fit_001.wav").write_bytes(b"z" * 2000)
    (out / "final.mp4").write_bytes(b"mp4")

    removed = pc.cleanup_after_dub_complete(out, session, keep_names={"final.mp4"})
    assert removed >= 0
    assert session.is_dir(), "session_dir must survive"
    assert (session / "tts_000.mp3").is_file()
    assert (session / "pause_run_000.wav").is_file()
    assert (session / "slot_fit_001.wav").is_file()


def test_assert_audio_file():
    from engines.pipeline_integrity.audio_presence import assert_audio_file
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.wav"
        p.write_bytes(b"x" * 500)
        try:
            assert_audio_file(p)
            raise AssertionError("expected FileNotFoundError for tiny file")
        except FileNotFoundError:
            pass
        p.write_bytes(b"y" * 1200)
        assert assert_audio_file(p) == p


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_cleanup_after_dub_keeps_session_and_audio(Path(td))
    test_assert_audio_file()
    print("ALL_GREEN")
