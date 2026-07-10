"""Prepare smoke test for EN→RU, EN→UK, RU→UK, UK→RU (mock Marian download)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAIRS = [
    ("en", "ru"),
    ("en", "uk"),
    ("ru", "uk"),
    ("uk", "ru"),
]


def _touch_marian(app_dir: Path, src: str, tgt: str) -> None:
    from engines.model_manager.integrity import model_id_to_folder
    from engines.model_manager.registry import touch_component
    from engines.model_manager.storage import hub_dir

    mid = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
    folder = hub_dir(app_dir) / model_id_to_folder(mid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "pytorch_model.bin").write_bytes(b"x" * 1024)
    touch_component(app_dir, "mt", f"marian:{src}-{tgt}", engine_hint="marian", artifact_id=mid)


def _fake_whisper(app_dir, size):
    from engines.model_manager.registry import touch_component
    from engines.model_manager.storage import hub_dir

    root = hub_dir(app_dir)
    d = root / f"models--Systran--faster-whisper-{size}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"w")
    touch_component(app_dir, "whisper", size, engine_hint="whisper")


def test_prepare_pairs_mocked():
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "data").mkdir(parents=True)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps(
                {
                    "pairs": {
                        "en->ru": ["marian", "argos"],
                        "en->uk": ["marian", "argos"],
                        "ru->uk": ["marian", "argos"],
                        "uk->ru": ["marian", "argos"],
                    }
                }
            ),
            encoding="utf-8",
        )
        from engines.model_manager import configure, ensure_profile, is_profile_ready

        configure(app, run_temp_cleanup=False)

        with patch("engines.model_manager.downloader.argos_pair_available", return_value=False):
            with patch("engines.model_manager.downloader.ensure_whisper", side_effect=_fake_whisper):
                with patch("engines.model_manager.downloader._ensure_marian", side_effect=_touch_marian):
                    with patch("engines.model_manager.downloader.verify_whisper", return_value=True):
                        with patch(
                            "engines.model_manager.downloader.is_mt_engine_ready",
                            side_effect=lambda a, e, s, t: e == "marian",
                        ):
                            for src, tgt in PAIRS:
                                plan_path = app / "output" / "dev"
                                r = ensure_profile(app, src, tgt, whisper_size="tiny", job_id=f"{src}-{tgt}")
                                assert r.ready, f"{src}->{tgt} failed: {r.error}"
                                assert is_profile_ready(app, src, tgt, whisper_size="tiny")
                                assert (plan_path / f"prepare_{src}-{tgt}.log").is_file()


def test_plan_route_is_fast():
    import time
    from pathlib import Path

    from engines.model_manager.profiles import profile_for_pair

    app = ROOT
    t = time.perf_counter()
    items = profile_for_pair(app, "en", "uk", whisper_size="tiny", feature="dub")
    elapsed = time.perf_counter() - t
    assert len(items) >= 3
    assert elapsed < 2.0, f"profile_for_pair too slow: {elapsed:.1f}s"


def main() -> int:
    test_plan_route_is_fast()
    test_prepare_pairs_mocked()
    print("prepare pairs tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
