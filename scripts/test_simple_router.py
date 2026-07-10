"""Minimal router stability tests (Router path — dev only)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["VM_USE_ROUTER"] = "1"
os.environ["VM_DEV_MODE"] = "1"


def _app_with_rankings(pairs: dict) -> Path:
    import tempfile as tf

    tmp = tf.mkdtemp()
    app = Path(tmp)
    (app / "data").mkdir(parents=True)
    (app / "data" / "mt_pair_rankings.json").write_text(
        json.dumps({"pairs": pairs}), encoding="utf-8"
    )
    (app / "data" / "mt_fallback_routes.json").write_text('{"pairs": {}}', encoding="utf-8")
    return app


def test_one_engine_per_pair():
    app = _app_with_rankings({"en->uk": ["marian"], "en->ru": ["marian"]})
    from engines.mt.registry import engines_for_pair

    p, f = engines_for_pair(app, "en", "uk")
    assert p == "marian"
    assert f is None


def test_no_quality_pivot_loop():
    app = _app_with_rankings({"en->uk": ["marian"]})

    def fake_leg(text, src, tgt, app_dir, segment_index=-1):
        if src == "en" and tgt == "uk":
            return "Hello світ mixed", {"engine": "marian", "engines_tried": ["marian"]}
        return "", {"engine": "none", "engines_tried": []}

    with patch("engines.translation_router._translate_leg", side_effect=fake_leg):
        from engines.translation_router import translate_with_router

        out, meta = translate_with_router("Hello world", "en", "uk", app_dir=app)
    assert out  # accepts even low quality — no pivot retry
    assert meta.get("route_label") == "en→uk"
    assert len(meta.get("routes_tried") or []) == 1


def test_fallback_route_on_empty_direct():
    app = _app_with_rankings({"en->uk": ["marian"]})
    (app / "data" / "mt_fallback_routes.json").write_text(
        json.dumps({"pairs": {"en->uk": [["en", "ru"], ["ru", "uk"]]}}),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def fake_leg(text, src, tgt, app_dir, segment_index=-1):
        calls.append((src, tgt))
        if src == "en" and tgt == "uk":
            return "", {"engine": "marian", "engines_tried": ["marian"]}
        if src == "en" and tgt == "ru":
            return "Привет", {"engine": "marian", "engines_tried": ["marian"]}
        if src == "ru" and tgt == "uk":
            return "Привіт", {"engine": "marian", "engines_tried": ["marian"]}
        return text, {"engine": "none", "engines_tried": []}

    with patch("engines.translation_router._translate_leg", side_effect=fake_leg):
        from engines.translation_router import translate_with_router

        out, meta = translate_with_router("Hello", "en", "uk", app_dir=app)
    assert out == "Привіт"
    assert meta.get("route") == "fallback"
    assert ("en", "ru") in calls


def test_mt_primary_only_no_cascade():
    app = _app_with_rankings({"en->uk": ["marian"]})
    mock_eng = MagicMock()
    mock_eng.id = "marian"
    mock_eng.translate.return_value = MagicMock(
        text="Привіт", engine_id="marian", engine_version="v", error="", elapsed_ms=1.0, offline=True
    )

    with patch("engines.mt.registry.get_engine_by_id", return_value=mock_eng):
        from engines.mt.registry import translate_with_best_engine

        out, meta = translate_with_best_engine("Hi", "en", "uk", app_dir=app)
    assert out == "Привіт"
    assert meta["engines_tried"] == ["marian"]
    assert mock_eng.translate.call_count == 1


def main() -> int:
    test_one_engine_per_pair()
    test_no_quality_pivot_loop()
    test_fallback_route_on_empty_direct()
    test_mt_primary_only_no_cascade()
    print("simple router stability tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
