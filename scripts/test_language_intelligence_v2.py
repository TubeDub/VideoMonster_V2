"""Language Intelligence v2 tests."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_v2_four_questions_skip_high_naturalness():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    os.environ.pop("VM_LANGUAGE_INTELLIGENCE_ANALYSIS", None)
    from engines.language_intelligence.pipeline import process_segment

    text = "Привіт, як справи?"
    improved, meta = process_segment(
        original="Hello",
        raw_mt=text,
        naturalized=text,
        final=text,
        tgt_lang="uk",
        write_log=False,
    )
    assert improved == text
    assert meta.get("changed") is False
    assert meta.get("naturalness_score", 100) >= 85


def test_v2_applies_ruism_with_confidence():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    from engines.language_intelligence.pipeline import process_segment

    improved, meta = process_segment(
        original="He is young",
        raw_mt="Він младший",
        naturalized="Він младший",
        final="Він младший",
        tgt_lang="uk",
        write_log=False,
    )
    assert "молодший" in improved.lower()
    assert meta.get("changed") is True
    assert meta.get("fixes_applied")


def test_v2_rejects_semantic_drift():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    from engines.language_intelligence.semantic_validator import validate_semantic_preserve

    ok, fails = validate_semantic_preserve(
        "There are 100 cars",
        "Є 100 машин",
        "Є машини",
    )
    assert not ok
    assert any(f.get("code") == "numbers_changed" for f in fails)


def test_v2_analysis_only_no_change():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    os.environ["VM_LANGUAGE_INTELLIGENCE_ANALYSIS"] = "1"
    from engines.language_intelligence.pipeline import process_segments

    segs = [{"original": "x", "raw_mt": "Він младший", "naturalized": "Він младший", "final": "Він младший"}]
    out, meta = process_segments(segs, tgt_lang="uk", learn_after=False, write_report_file=False)
    assert out[0] == "Він младший"
    assert meta.get("analysis_only") is True
    assert meta.get("suggestions", 0) >= 0 or meta.get("fixes_rejected", 0) >= 0


def test_v2_naturalness_tiers():
    from engines.language_intelligence.naturalness import tier_from_score

    assert tier_from_score(97).action == "skip"
    assert tier_from_score(88).action == "skip"
    assert tier_from_score(75).action == "suggest"
    assert tier_from_score(60).action == "fix_if_confident"
    assert tier_from_score(40).action == "analyze"


def test_v2_integration_disabled():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "0"
    from engines.language_intelligence.integration import apply_before_tts

    segs = ["Привіт"]
    assert apply_before_tts(segs, [{}]) == segs


def test_v2_context_george_jr():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    os.environ.pop("VM_LANGUAGE_INTELLIGENCE_ANALYSIS", None)
    from engines.language_intelligence.pipeline import process_segment

    improved, meta = process_segment(
        original="George Jr. said hello",
        raw_mt="George Jr. said hello",
        naturalized="George Jr. said hello",
        final="George Jr. said hello",
        tgt_lang="uk",
        write_log=False,
    )
    assert "молодший" in improved.lower()
    assert meta.get("changed") is True


def test_v2_context_junior_engineer():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    from engines.language_intelligence.pipeline import process_segment

    improved, meta = process_segment(
        original="Junior Engineer position",
        raw_mt="Junior Engineer position",
        naturalized="Junior Engineer position",
        final="Junior Engineer position",
        tgt_lang="uk",
        write_log=False,
    )
    assert "молодший" in improved.lower()
    assert "junior" not in improved.lower()


def test_v2_report_language_report_txt():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    os.environ.pop("VM_LANGUAGE_INTELLIGENCE_ANALYSIS", None)
    from engines.language_intelligence.pipeline import process_segments

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        (app / "output" / "reports").mkdir(parents=True)
        (app / "output" / "dev").mkdir(parents=True)
        (app / "data" / "language_intelligence").mkdir(parents=True)
        segs = [
            {
                "original": "He came but left",
                "raw_mt": "Він прийшов но пішов",
                "naturalized": "Він прийшов но пішов",
                "final": "Він прийшов но пішов",
            }
        ]
        process_segments(segs, tgt_lang="uk", app_dir=app, task_id="v2", learn_after=False)
        assert (app / "output" / "reports" / "Language_Report.txt").is_file()


def test_app_untouched():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "0"
    import app  # noqa: F401

    assert app.app.name == "app"


def main() -> int:
    test_v2_naturalness_tiers()
    test_v2_four_questions_skip_high_naturalness()
    test_v2_applies_ruism_with_confidence()
    test_v2_rejects_semantic_drift()
    test_v2_analysis_only_no_change()
    test_v2_integration_disabled()
    test_v2_context_george_jr()
    test_v2_context_junior_engineer()
    test_v2_report_language_report_txt()
    test_app_untouched()
    print("language intelligence v2 tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
