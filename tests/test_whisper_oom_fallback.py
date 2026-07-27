"""STT OOM / mkl_malloc recovery — fall back to smaller Whisper sizes."""

from __future__ import annotations

import sys
import types


def test_whisper_fallback_sizes_order():
    from engines.model_manager.downloader import _whisper_fallback_sizes

    assert _whisper_fallback_sizes("medium") == ["medium", "small", "base", "tiny"]
    assert _whisper_fallback_sizes("tiny") == ["tiny"]
    assert "tiny" in _whisper_fallback_sizes("large-v3")


def test_is_whisper_oom_detects_mkl():
    from engines.model_manager.downloader import _is_whisper_oom

    assert _is_whisper_oom(RuntimeError("mkl_malloc: failed to allocate memory"))
    assert _is_whisper_oom(RuntimeError("CUDA out of memory"))
    assert not _is_whisper_oom(RuntimeError("model not found"))


def test_load_whisper_falls_back_on_oom(monkeypatch, tmp_path):
    from engines.model_manager import downloader as dl

    dl.clear_whisper_cache()
    calls: list[tuple[str, str, str]] = []

    class FakeModel:
        def __init__(self, size, device="cpu", compute_type="int8", download_root=None, **kwargs):
            calls.append((size, device, compute_type))
            if size in ("medium", "small"):
                raise RuntimeError("mkl_malloc: failed to allocate memory")

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    monkeypatch.setattr(dl, "verify_whisper", lambda app_dir, size: True)
    monkeypatch.setattr(
        "engines.hardware_probe.probe_whisper_device",
        lambda: ("cuda", "float16"),
    )
    monkeypatch.setattr(dl, "touch_component", lambda *a, **k: None)
    monkeypatch.setattr(dl, "hub_dir", lambda app_dir: tmp_path)

    model = dl.load_whisper(tmp_path, "medium")
    assert isinstance(model, FakeModel)
    assert calls[-1][0] == "base"


def test_load_whisper_falls_back_when_requested_missing_offline(monkeypatch, tmp_path):
    """CJK bump to «small» must not kill STT when only «tiny» is on disk."""
    from engines.model_manager import downloader as dl

    dl.clear_whisper_cache()
    loaded: list[str] = []

    class FakeModel:
        def __init__(self, size, device="cpu", compute_type="int8", download_root=None, **kwargs):
            loaded.append(size)

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    monkeypatch.setattr(
        dl, "verify_whisper", lambda app_dir, size: size in ("base", "tiny")
    )
    monkeypatch.setattr(dl, "is_offline_only", lambda: False)
    monkeypatch.setattr(dl, "downloads_permitted", lambda: False)
    monkeypatch.setattr(
        "engines.hardware_probe.probe_whisper_device",
        lambda: ("cpu", "int8"),
    )
    monkeypatch.setattr(dl, "touch_component", lambda *a, **k: None)
    monkeypatch.setattr(dl, "hub_dir", lambda app_dir: tmp_path)

    model = dl.load_whisper(tmp_path, "small")
    assert isinstance(model, FakeModel)
    assert loaded == ["base"]
    # Alias cache so a second «small» request does not re-walk
    assert "small" in dl._WHISPER_CACHE
    assert "base" in dl._WHISPER_CACHE


def test_cjk_upgrade_failure_keeps_tiny_pass(monkeypatch):
    """Recursive CJK upgrade must not abort an already-running tiny STT."""
    from engines import stt_engine as stt

    class _Seg:
        def __init__(self):
            self.text = "你好"
            self.start = 0.0
            self.end = 1.0

    class _Info:
        language = "zh"

    class _Model:
        def transcribe(self, *a, **k):
            return iter([_Seg()]), _Info()

    calls: list[str] = []

    def fake_get(size: str):
        calls.append(size)
        if size == "small":
            from engines.model_manager.runtime import ModelNotPreparedError

            raise ModelNotPreparedError("Whisper small не установлен", component="whisper")
        return _Model()

    monkeypatch.setattr(stt, "_get_faster_model", fake_get)
    monkeypatch.setattr(stt, "_best_prepared_cjk_model", lambda requested="tiny": "small")

    text, _srt, timing, lang = stt._transcribe_faster_whisper(
        "dummy.wav", language=None, model_size="tiny"
    )
    assert text == "你好"
    assert lang == "zh"
    assert timing and timing[0]["start"] == 0
    assert calls == ["tiny", "small"]
