"""TubeDub AI Manager tests."""

from __future__ import annotations

import json
from pathlib import Path


def test_prompt_needed_respects_defer(tmp_path, monkeypatch):
    from engines.ai_manager import manager
    from engines.ai_manager.config import save_config, default_config

    # Deterministic: no real local AI on the dev box should flip this.
    monkeypatch.setattr(manager, "is_ai_ready", lambda *_a, **_k: False)

    cfg = default_config()
    cfg["deferred"] = True
    save_config(tmp_path, cfg)
    assert manager.prompt_needed(tmp_path, quality_maximum=False) is False
    assert manager.prompt_needed(tmp_path, quality_maximum=True) is True


def test_defer_sets_flag(tmp_path):
    from engines.ai_manager import defer_install
    from engines.ai_manager.config import load_config

    defer_install(tmp_path)
    assert load_config(tmp_path).get("deferred") is True


def test_user_status_no_technical_terms(tmp_path):
    from engines.ai_manager import user_status

    st = user_status(tmp_path)
    assert "Ollama" not in json.dumps(st, ensure_ascii=False)
    assert "LM Studio" not in json.dumps(st, ensure_ascii=False)
    assert st["status_label"] in ("Не установлен", "Устанавливается", "Готов к работе", "Ошибка установки")


def test_openddf_ai_installation_block(tmp_path):
    from engines.ai_manager.manager import build_openddf_ai_installation

    block = build_openddf_ai_installation(tmp_path, {})
    assert "backend_label" in block
    assert "Ollama" not in json.dumps(block, ensure_ascii=False)


def test_installer_rejects_small_download(tmp_path, monkeypatch):
    from engines.ai_manager import installer

    dest = tmp_path / "setup.exe"

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"tiny"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp())
    try:
        installer._download_installer(dest, tmp_path, max_retries=1)
        assert False, "should raise"
    except RuntimeError:
        pass


def test_suppress_backend_gui_is_safe(tmp_path):
    """Must never raise; on non-Windows it is a clean no-op."""
    from engines.ai_manager import installer

    installer.suppress_backend_gui(tmp_path)
    # CLI binary name must not be in the GUI kill list (would kill our server).
    assert "ollama.exe" not in [n.lower() for n in installer._GUI_PROCESS_NAMES]


def test_ensure_backend_headless_no_binary(tmp_path, monkeypatch):
    from engines.ai_manager import installer

    monkeypatch.setattr(installer, "find_ollama_binary", lambda: None)
    assert installer.ensure_backend_headless(tmp_path) is False


def test_prepare_status_shows_components():
    from engines.system_prepare import get_prepare_status, start_background_prepare

    start_background_prepare(force=True)
    import time

    time.sleep(0.5)
    st = get_prepare_status()
    labels = {c["label"] for c in st["components"]}
    assert "FFmpeg" in labels
    assert "Whisper" in labels
    assert "Marian MT" in labels
    assert "Ollama / Qwen" in labels
    assert "overall_percent" in st
    assert "log_path" in st
