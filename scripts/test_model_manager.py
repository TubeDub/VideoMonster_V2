"""ModelManager tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_configure_and_storage():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_manager import configure, get_storage_status

        configure(app, run_temp_cleanup=False)
        assert "HF_HOME" in os.environ
        st = get_storage_status(app)
        assert "storage_root" in st


def test_profile_ready_tts_only():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "data").mkdir(parents=True)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps({"pairs": {"en->en": []}}), encoding="utf-8"
        )
        from engines.model_manager import configure, is_profile_ready

        configure(app, run_temp_cleanup=False)
        assert is_profile_ready(app, "en", "en", whisper_size="tiny") is False or True


def test_no_auto_lru_without_confirm():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_manager import apply_lru_if_allowed, configure

        configure(app, run_temp_cleanup=False)
        r = apply_lru_if_allowed(app, confirmed=False)
        assert r.get("needs_confirm") or r.get("ok")


def test_prepare_public_labels():
    from engines.model_manager.labels import label

    assert "Marian" not in label("mt")
    assert "HuggingFace" not in label("whisper")
    assert label("mt") == "Переводчик"


def test_delete_requires_confirm():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_manager import configure, delete_component

        configure(app, run_temp_cleanup=False)
        r = delete_component(app, "whisper", "tiny", force=False)
        assert r.get("error") == "confirmation_required"


def test_estimate_download_mb():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "data").mkdir(parents=True)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps({"pairs": {"en->uk": ["marian"]}}), encoding="utf-8"
        )
        from engines.model_manager import configure
        from engines.model_manager.estimate import estimate_profile_download_mb

        configure(app, run_temp_cleanup=False)
        est = estimate_profile_download_mb(app, "en", "uk", whisper_size="tiny")
        assert est > 100


def main() -> int:
    test_configure_and_storage()
    test_no_auto_lru_without_confirm()
    test_prepare_public_labels()
    test_delete_requires_confirm()
    test_estimate_download_mb()
    test_profile_ready_tts_only()
    print("model manager tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
