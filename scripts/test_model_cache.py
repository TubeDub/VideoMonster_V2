"""Tests for model cache manager — install, cleanup, LRU, delete."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_configure_hf_cache_redirects_env() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_manager import configure, get_storage_status

        configure(app, run_temp_cleanup=False)
        assert os.environ["HF_HOME"]
        st = get_storage_status(app)
        assert "storage_root" in st


def test_cleanup_temp_removes_incomplete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_manager.integrity import cleanup_temp_files
        from engines.model_manager import configure
        from engines.model_manager.storage import tmp_dir

        configure(app, run_temp_cleanup=False)
        bad = tmp_dir(app) / "chunk.bin.incomplete"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"x" * 100)
        result = cleanup_temp_files(app)
        assert result["removed_files"] >= 1
        assert not bad.exists()


def test_lru_eviction() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_manager import apply_lru_if_allowed, configure, set_max_storage_gb
        from engines.model_manager.registry import touch_component
        from engines.model_manager.storage import hub_dir

        configure(app, run_temp_cleanup=False)
        set_max_storage_gb(app, 0.000001)

        hub = hub_dir(app)
        old = hub / "models--Org--Old"
        new = hub / "models--Org--New"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        (old / "model.bin").write_bytes(b"o" * 800)
        (new / "model.bin").write_bytes(b"n" * 800)

        touch_component(app, "mt", "old", artifact_id="Org/Old", engine_hint="test")
        touch_component(app, "mt", "new", artifact_id="Org/New", engine_hint="test")

        reg_path = app / "data" / "model_cache_registry.json"
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg.setdefault("models", {})["Org/Old"] = {"last_used": "2020-01-01T00:00:00Z"}
        reg["models"]["Org/New"] = {"last_used": "2026-01-01T00:00:00Z"}
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

        result = apply_lru_if_allowed(app, confirmed=True)
        assert result.get("ok") or result.get("deleted")


def test_delete_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_cache import configure_hf_cache, delete_model
        from engines.model_manager.storage import hub_dir

        configure_hf_cache(app, run_cleanup=False)
        folder = hub_dir(app) / "models--Test--Model"
        folder.mkdir(parents=True)
        (folder / "x.bin").write_bytes(b"12345")
        result = delete_model(app, "Test/Model")
        assert result["ok"]
        assert not folder.exists()


def test_cache_status_disk_info() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        from engines.model_cache import cache_status, configure_hf_cache
        from engines.model_manager.storage import is_configured

        configure_hf_cache(app, run_cleanup=False)
        st = cache_status(app)
        assert "cache_mb" in st
        assert st.get("disk_free_gb") is not None
        assert st.get("configured")


def test_models_needed_for_pair() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "data").mkdir(parents=True)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps({"pairs": {"en->uk": ["marian", "argos"]}}),
            encoding="utf-8",
        )
        from engines.model_cache import models_needed_for_pair

        ids = models_needed_for_pair(app, "en", "uk")
        assert "Helsinki-NLP/opus-mt-en-uk" in ids


def main() -> int:
    test_configure_hf_cache_redirects_env()
    test_cleanup_temp_removes_incomplete()
    test_lru_eviction()
    test_delete_model()
    test_cache_status_disk_info()
    test_models_needed_for_pair()
    print("model cache tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
