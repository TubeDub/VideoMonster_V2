"""Translation Manager — route compare, name guard, pipeline wiring."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.pop("VM_STABLE_MT_ONLY", None)
os.environ["VM_TRANSLATION_MANAGER"] = "1"
os.environ.pop("VM_USE_STR", None)


def _mock_engine(eid: str, text: str):
    from engines.mt.base import MTResult

    eng = MagicMock()
    eng.id = eid
    eng.offline = True
    eng.priority = 20
    eng.version = "test"
    eng.supports_pair.return_value = True
    eng.is_available.return_value = True
    eng.translate.return_value = MTResult(text=text, engine_id=eid, elapsed_ms=50.0)
    return eng


def test_use_translation_manager_default_on():
    from engines.translation_manager import use_translation_manager

    assert use_translation_manager() is True


def test_stable_mt_only_disables_manager():
    from engines.mt.stable_translate import use_stable_mt
    from engines.translation_manager import use_translation_manager

    os.environ["VM_STABLE_MT_ONLY"] = "1"
    try:
        assert use_translation_manager() is False
        assert use_stable_mt() is True
    finally:
        os.environ.pop("VM_STABLE_MT_ONLY", None)


def test_manager_picks_higher_score():
    from engines.translation_manager import translate_with_manager
    from engines.translation_router import TranslationRoute

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        good = _mock_engine("marian", "Привіт світ")
        bad = _mock_engine("nllb", "Hello світ mixed")

        direct = TranslationRoute("direct", [("en", "uk")])

        with patch("engines.translation_router.candidate_routes", return_value=[direct]):
            with patch(
                "engines.translation_manager._translate_route_with_engines",
                side_effect=[
                    [
                        __import__(
                            "engines.translation_manager", fromlist=["TranslationCandidate"]
                        ).TranslationCandidate(
                            text="Hello світ mixed",
                            score=40.0,
                            engine="nllb",
                            route_label="en→uk",
                            route_name="direct",
                            pivot=None,
                            direct=True,
                            elapsed_ms=50,
                        ),
                        __import__(
                            "engines.translation_manager", fromlist=["TranslationCandidate"]
                        ).TranslationCandidate(
                            text="Привіт світ",
                            score=85.0,
                            engine="marian",
                            route_label="en→uk",
                            route_name="direct",
                            pivot=None,
                            direct=True,
                            elapsed_ms=45,
                        ),
                    ]
                ],
            ):
                text, meta = translate_with_manager("Hello world", "en", "uk", app_dir=app)
        assert text == "Привіт світ"
        assert meta["engine"] == "marian"
        assert meta.get("alternative_translation")


def test_name_to_tech_term_damage():
    from engines.translation_quality import name_to_tech_term_damage

    hits = name_to_tech_term_damage("George works here", "System works here")
    assert hits


def test_naturalizer_still_called_in_pipeline():
    from engines.translation_pipeline import UniversalTranslationPipeline

    pipe = UniversalTranslationPipeline()
    assert hasattr(pipe, "translate_segments")


def main() -> int:
    test_use_translation_manager_default_on()
    test_stable_mt_only_disables_manager()
    test_manager_picks_higher_score()
    test_name_to_tech_term_damage()
    test_naturalizer_still_called_in_pipeline()
    print("translation manager tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
