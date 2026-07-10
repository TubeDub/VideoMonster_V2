"""Stable MT path tests — default production mode."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Ensure stable mode (legacy Marian-only)
os.environ.pop("VM_USE_ROUTER", None)
os.environ.pop("VM_DEV_MODE", None)
os.environ["VM_STABLE_MT_ONLY"] = "1"
os.environ["VM_TRANSLATION_MANAGER"] = "0"


def test_stable_mode_default():
    from engines.mt.stable_translate import use_stable_mt

    assert use_stable_mt() is True


def test_translate_text_traced_uses_marian():
    mock_tok = MagicMock()
    mock_model = MagicMock()
    mock_model.generate.return_value = MagicMock()
    mock_tok.return_value = {"input_ids": MagicMock()}
    mock_tok.decode.return_value = "Привіт"

    with patch("engines.model_manager.downloader.load_marian", return_value=(mock_tok, mock_model, "m")):
        with patch("engines.model_manager.downloader.is_mt_engine_ready", return_value=True):
            from engines.translation import translate_text_traced

            out, meta = translate_text_traced("Hello", "en", "uk", app_dir=ROOT)
    assert out == "Привіт"
    assert meta.get("stable_mt") is True
    assert meta.get("engine") == "marian"


def main() -> int:
    test_stable_mode_default()
    test_translate_text_traced_uses_marian()
    print("stable translate tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
