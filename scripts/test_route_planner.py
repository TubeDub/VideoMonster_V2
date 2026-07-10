"""Route planner + resilient prepare tests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _seed_app(tmp: str) -> Path:
    app = Path(tmp)
    (app / "data").mkdir(parents=True)
    rankings = {
        "pairs": {
            "en->uk": ["marian", "argos"],
            "en->ru": ["marian", "argos"],
            "ru->uk": ["marian", "argos"],
            "uk->ru": ["marian", "argos"],
        }
    }
    (app / "data" / "mt_pair_rankings.json").write_text(json.dumps(rankings), encoding="utf-8")
    return app


def test_en_uk_prepare_excludes_pivot_ru_uk():
    with tempfile.TemporaryDirectory() as tmp:
        app = _seed_app(tmp)
        from engines.model_manager import configure
        from engines.model_manager.route_planner import plan_translation_requirements

        configure(app, run_temp_cleanup=False)
        plan = plan_translation_requirements(app, "en", "uk")
        assert plan.primary_route == "en→uk"
        assert plan.prepare_legs == [("en", "uk")]
        pairs = {(r.src, r.tgt) for r in plan.mt_requirements}
        assert ("ru", "uk") not in pairs
        assert ("en", "uk") in pairs or not plan.mt_requirements


def test_ensure_mt_leg_skips_missing_argos():
    with tempfile.TemporaryDirectory() as tmp:
        app = _seed_app(tmp)
        (app / "data" / "mt_pair_rankings.json").write_text(
            json.dumps({"pairs": {"ru->uk": ["argos", "marian"]}}),
            encoding="utf-8",
        )
        from engines.model_manager import configure
        from engines.model_manager.downloader import ensure_mt_leg

        configure(app, run_temp_cleanup=False)

        def fake_marian(a, eng, s, t):
            from engines.model_manager.registry import touch_component

            touch_component(a, "mt", f"marian:{s}-{t}", engine_hint="marian")
            return None

        with patch("engines.model_manager.downloader.argos_pair_available", return_value=False):
            with patch("engines.model_manager.downloader.ensure_mt_engine", side_effect=fake_marian):
                r = ensure_mt_leg(app, "ru", "uk")
        assert r["ok"]
        assert any("argos" in n and "marian" in n for n in r.get("notes", []))


def test_profile_one_mt_leg_per_direct_route():
    with tempfile.TemporaryDirectory() as tmp:
        app = _seed_app(tmp)
        from engines.model_manager import configure
        from engines.model_manager.profiles import profile_for_pair

        configure(app, run_temp_cleanup=False)
        items = profile_for_pair(app, "en", "uk")
        mt = [i for i in items if i.component_id == "mt"]
        assert len(mt) == 1
        assert mt[0].src_lang == "en" and mt[0].tgt_lang == "uk"


def main() -> int:
    test_en_uk_prepare_excludes_pivot_ru_uk()
    test_ensure_mt_leg_skips_missing_argos()
    test_profile_one_mt_leg_per_direct_route()
    print("route planner tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
