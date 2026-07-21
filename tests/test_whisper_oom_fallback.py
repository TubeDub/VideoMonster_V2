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
