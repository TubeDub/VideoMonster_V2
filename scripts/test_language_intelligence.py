"""Language Intelligence module tests — no pipeline integration required."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_disabled_passthrough():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "0"
    from engines.language_intelligence import is_enabled, process_segments

    assert not is_enabled()
    segs = [{"original": "Hi", "raw_mt": "Привіт", "naturalized": "Привіт", "final": "Привіт"}]
    out, meta = process_segments(segs, tgt_lang="uk")
    assert out[0] == "Привіт"
    assert meta.get("enabled") is False


def test_fixes_ruism():
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


def test_fiat_and_star_wars():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    from engines.language_intelligence.pipeline import process_segment

    improved, _ = process_segment(
        original="Star Wars and Fiat",
        raw_mt="Star Wars і Фіат",
        naturalized="Star Wars і Фіат",
        final="Star Wars і Фіат",
        tgt_lang="uk",
        write_log=False,
    )
    assert "Fiat" in improved
    assert "Зоряні війни" in improved


def test_natural_unchanged():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
    from engines.language_intelligence.pipeline import process_segment

    text = "Привіт, як справи?"
    improved, meta = process_segment(
        original="Hello, how are you?",
        raw_mt=text,
        naturalized=text,
        final=text,
        tgt_lang="uk",
        write_log=False,
    )
    assert improved == text
    assert meta.get("changed") is False


def test_report_written():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "1"
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
        out, meta = process_segments(
            segs, tgt_lang="uk", app_dir=app, task_id="test", learn_after=False
        )
        assert "але" in out[0].lower()
        report = app / "output" / "reports" / "LANGUAGE_INTELLIGENCE_REPORT.txt"
        assert report.is_file()
        assert meta.get("changed", 0) >= 1


def test_app_still_imports():
    os.environ["VM_LANGUAGE_INTELLIGENCE"] = "0"
    import app  # noqa: F401

    assert app.app.name == "app"


def main() -> int:
    test_disabled_passthrough()
    test_fixes_ruism()
    test_fiat_and_star_wars()
    test_natural_unchanged()
    test_report_written()
    test_app_still_imports()
    print("language intelligence tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
