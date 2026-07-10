"""Smart Translation Router + Quality Score + Split tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["VM_USE_ROUTER"] = "1"
os.environ["VM_DEV_MODE"] = "1"

from engines.translation_quality_score import (
    compute_quality_score,
    should_switch_route,
)
from engines.translation_router import (
    TranslationRoute,
    candidate_routes,
)
from engines.translation_split import merge_translated_parts, split_for_translation


def test_quality_en_ru_better_than_mixed() -> None:
    good, _ = compute_quality_score(
        "Hello world",
        "Привет мир",
        src_lang="en",
        tgt_lang="ru",
    )
    bad, metrics = compute_quality_score(
        "Hello world",
        "Hello світ mixed text",
        src_lang="en",
        tgt_lang="uk",
    )
    assert good > bad
    assert should_switch_route(bad, metrics)


def test_split_long_sentence() -> None:
    text = (
        "This is a very long sentence that should be split before translation "
        "because it contains many words and also has, a comma and because it "
        "goes on and on with additional clauses that exceed the limit."
    )
    parts = split_for_translation(text)
    assert len(parts) >= 2
    for p in parts:
        assert len(p.split()) <= 20
    merged = merge_translated_parts(["Part one.", "Part two."])
    assert "Part one" in merged and "Part two" in merged


def test_candidate_routes_still_available_for_dev() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        routes = candidate_routes("en", "uk", app)
        assert routes[0].is_direct
        assert len(routes) >= 2


def test_fallback_route_on_hard_failure_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "data").mkdir(parents=True)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps({"pairs": {"en->uk": ["marian"]}}), encoding="utf-8"
        )
        (app / "data" / "mt_fallback_routes.json").write_text(
            json.dumps({"pairs": {"en->uk": [["en", "ru"], ["ru", "uk"]]}}),
            encoding="utf-8",
        )

        def fake_leg(text, src, tgt, app_dir, segment_index=-1):
            if src == "en" and tgt == "uk":
                return "", {"engine": "marian", "engines_tried": ["marian"]}
            if src == "en" and tgt == "ru":
                return "Привет", {"engine": "marian", "engines_tried": ["marian"]}
            if src == "ru" and tgt == "uk":
                return "Привіт", {"engine": "marian", "engines_tried": ["marian"]}
            return text, {"engine": "none", "engines_tried": []}

        with patch("engines.translation_router._translate_leg", side_effect=fake_leg):
            from engines.translation_router import translate_with_router

            out, meta = translate_with_router("Hello world", "en", "uk", app_dir=app)
        assert "Привіт" in out
        assert meta.get("route") == "fallback"


def test_low_quality_does_not_trigger_pivot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "data").mkdir(parents=True)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps({"pairs": {"en->uk": ["marian"]}}), encoding="utf-8"
        )

        def fake_leg(text, src, tgt, app_dir, segment_index=-1):
            return "Hello світ mixed", {"engine": "marian", "engines_tried": ["marian"]}

        with patch("engines.translation_router._translate_leg", side_effect=fake_leg):
            from engines.translation_router import translate_with_router

            out, meta = translate_with_router("Hello world", "en", "uk", app_dir=app)
        assert out
        assert meta.get("route_label") == "en→uk"
        assert len(meta.get("routes_tried") or []) == 1


def test_cache_version_bump() -> None:
    from engines.pipeline_cache import CACHE_VERSION, ROUTER_VERSION, segments_fingerprint

    assert CACHE_VERSION >= 2
    assert ROUTER_VERSION >= 5
    k1 = segments_fingerprint(["Hi"], "en", "uk")
    k2 = segments_fingerprint(["Hi"], "en", "uk", route_label="en→ru→uk")
    assert k1 != k2


def main() -> int:
    test_quality_en_ru_better_than_mixed()
    test_split_long_sentence()
    test_candidate_routes_still_available_for_dev()
    test_fallback_route_on_hard_failure_only()
    test_low_quality_does_not_trigger_pivot()
    test_cache_version_bump()
    print("translation router v2 tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
