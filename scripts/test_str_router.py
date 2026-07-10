"""Smart Translation Router (STR) tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["VM_USE_STR"] = "1"


def _mock_engine(eid: str, text: str, offline: bool = True, priority: int = 50):
    eng = MagicMock()
    eng.id = eid
    eng.offline = offline
    eng.priority = priority
    eng.version = "test"
    eng.supports_pair.return_value = True
    from engines.mt.base import MTResult

    eng.translate.return_value = MTResult(
        text=text,
        engine_id=eid,
        elapsed_ms=120.0,
        offline=offline,
    )
    return eng


def test_knowledge_base_records_stats():
    from engines.str.knowledge_base import engine_stats, record_translation

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        record_translation(
            app,
            src_lang="en",
            tgt_lang="uk",
            engine_id="marian",
            quality_score=84.0,
            elapsed_ms=200.0,
            mixed_language_pct=2.0,
            success=True,
            source_text="Hello world",
        )
        record_translation(
            app,
            src_lang="en",
            tgt_lang="uk",
            engine_id="nllb",
            quality_score=92.0,
            elapsed_ms=350.0,
            mixed_language_pct=1.0,
            success=True,
            source_text="Hello world",
        )
        m = engine_stats(app, "en", "uk", "marian")
        n = engine_stats(app, "en", "uk", "nllb")
        assert m["avg_quality"] == 84.0
        assert n["avg_quality"] == 92.0
        assert n["avg_quality"] > m["avg_quality"]


def test_ranking_prefers_better_history():
    from engines.str.knowledge_base import record_translation
    from engines.str.ranking import engine_order_ids

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        for _ in range(5):
            record_translation(
                app, src_lang="en", tgt_lang="uk", engine_id="marian",
                quality_score=84.0, elapsed_ms=100.0, success=True,
            )
        for _ in range(5):
            record_translation(
                app, src_lang="en", tgt_lang="uk", engine_id="nllb",
                quality_score=92.0, elapsed_ms=200.0, success=True,
            )

        marian = _mock_engine("marian", "Marian text")
        nllb = _mock_engine("nllb", "NLLB text")

        with patch("engines.str.adapters.list_available_engines", return_value=[marian, nllb]):
            order, reason = engine_order_ids(app, "en", "uk")
        assert order[0] == "nllb"
        assert "str_rank" in reason


def test_router_retries_on_poor_quality():
    from engines.str.router import translate_with_str

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        bad = _mock_engine("marian", "Hello світ mixed", priority=20)
        good = _mock_engine("nllb", "Привіт світ", priority=30)

        with patch("engines.str.adapters.list_available_engines", return_value=[bad, good]):
            text, meta = translate_with_str(
                "Hello world",
                "en",
                "uk",
                app_dir=app,
            )
        assert meta["engine"] == "nllb"
        assert meta["mt_retries"] >= 1
        assert "nllb" in meta["engines_tried"]
        assert text == "Привіт світ"


def test_router_accepts_good_first_engine():
    from engines.str.router import translate_with_str

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        good = _mock_engine("marian", "Привіт світ")

        with patch("engines.str.adapters.list_available_engines", return_value=[good]):
            text, meta = translate_with_str("Hello world", "en", "uk", app_dir=app)
        assert meta["engine"] == "marian"
        assert meta["mt_retries"] == 0
        assert meta["quality_score"] >= 55
        assert text == "Привіт світ"


def test_translate_text_traced_uses_str():
    from engines.str.config import use_str
    from engines.translation import translate_text_traced

    assert use_str()

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        eng = _mock_engine("marian", "Привіт")

        with patch("engines.str.adapters.list_available_engines", return_value=[eng]):
            text, meta = translate_text_traced("Hi", "en", "uk", app_dir=app)
        assert meta.get("str") is True
        assert text == "Привіт"


def test_diagnostics_detects_degrading():
    from engines.str.diagnostics import engine_trend
    from engines.str.knowledge_base import load_knowledge_base, save_knowledge_base

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        kb = load_knowledge_base(app)
        kb["pairs"] = {
            "en->uk": {
                "marian": {
                    "attempts": 10,
                    "successes": 10,
                    "errors": 0,
                    "total_quality": 900.0,
                    "total_speed_ms": 1000.0,
                    "total_mixed_pct": 0.0,
                    "total_retries": 0,
                    "recent_scores": [90, 88, 85, 82, 80, 78, 75, 72, 70, 68],
                }
            }
        }
        save_knowledge_base(app, kb)
        trend = engine_trend(app, "en", "uk", "marian")
        assert trend["degrading"] is True
        assert trend["direction"] == "degrading"


def test_str_result_uniform_shape():
    from engines.str.types import STRTranslationResult

    r = STRTranslationResult(
        text="Привіт",
        engine_id="marian",
        src_lang="en",
        tgt_lang="uk",
        elapsed_ms=100.0,
        warnings=["mixed_language:5%"],
        quality_probability=0.85,
    )
    m = r.to_meta()
    assert m["engine"] == "marian"
    assert m["quality_probability"] == 0.85
    assert m["warnings"] == ["mixed_language:5%"]


def main() -> int:
    test_knowledge_base_records_stats()
    test_ranking_prefers_better_history()
    test_router_retries_on_poor_quality()
    test_router_accepts_good_first_engine()
    test_translate_text_traced_uses_str()
    test_diagnostics_detects_degrading()
    test_str_result_uniform_shape()
    print("STR router tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
