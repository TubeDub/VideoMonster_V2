#!/usr/bin/env python3
"""Tests for AI Media Platform modules (TZ Etap 1–10 skeleton)."""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _write_silent_wav(path: Path, *, seconds: float = 0.5) -> None:
    rate = 16000
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)


def test_platform_config() -> None:
    os.environ.pop("VM_PLATFORM_ENABLED", None)
    from engines.platform.config import platform_status

    st = platform_status()
    assert "modules" in st
    assert st["modules"]["live"] is False


def test_platform_config_master_flag() -> None:
    os.environ["VM_PLATFORM_ENABLED"] = "1"
    from importlib import reload
    import engines.platform.config as cfg

    reload(cfg)
    assert cfg.live_translation_enabled()
    os.environ.pop("VM_PLATFORM_ENABLED", None)
    reload(cfg)


def test_diagnostics_sink() -> None:
    from engines.platform_diagnostics.sink import PlatformTraceSink

    os.environ["VM_PLATFORM_DIAGNOSTICS"] = "1"
    sink = PlatformTraceSink(ROOT, module="live", session_id="test123")
    sink.log(stage="test.stage", input_preview="in", output_preview="out", duration_ms=1.0)
    snap = sink.snapshot()
    assert snap["record_count"] >= 1
    assert Path(snap["path"]).is_file()


def test_ingest_file_missing() -> None:
    from engines.live.ingest import resolve_ingest

    r = resolve_ingest("Z:\\nonexistent_file_xxx.mp4", work_dir=ROOT / "output" / "dev")
    assert not r.ok


def test_voice_training_analyzer() -> None:
    os.environ["VM_PLATFORM_DIAGNOSTICS"] = "1"
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "t.wav"
        _write_silent_wav(wav, seconds=1.0)
        from engines.voice_training.analyzer import analyze_voice_recording

        result = analyze_voice_recording(
            str(wav), script="hello world", app_dir=ROOT, session_id="vt1"
        )
        assert isinstance(result.recommendations, list)


def test_vocal_training_analyzer() -> None:
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "v.wav"
        _write_silent_wav(wav, seconds=1.0)
        from engines.vocal_training.analyzer import analyze_vocal_recording

        result = analyze_vocal_recording(str(wav), app_dir=ROOT, session_id="vc1")
        assert result.score >= 0


def test_ai_assistant_rules() -> None:
    from engines.ai_assistant.analyzer import analyze_translation_review_segment

    issues = analyze_translation_review_segment(
        source="George Jr.",
        translated="Джордж-молодший",
        router_reason="en→ru",
    )
    assert any(i.get("why") == "uk_calque" for i in issues)


def test_media_browser_service() -> None:
    os.environ["VM_PLATFORM_ENABLED"] = "1"
    from engines.media_browser.service import MediaBrowserService

    svc = MediaBrowserService(ROOT)
    r = svc.open("https://example.com/video", tgt_lang="ru")
    assert r.get("ok")
    assert r.get("session_id")


def main() -> None:
    tests = [
        test_platform_config,
        test_platform_config_master_flag,
        test_diagnostics_sink,
        test_ingest_file_missing,
        test_voice_training_analyzer,
        test_vocal_training_analyzer,
        test_ai_assistant_rules,
        test_media_browser_service,
    ]
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"OK  {name}")
        except Exception as e:
            print(f"FAIL {name}: {e}")
            raise
    print("All platform tests passed.")


if __name__ == "__main__":
    main()
