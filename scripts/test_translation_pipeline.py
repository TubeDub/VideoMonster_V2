"""Translation Pipeline stage invariants + diagnostics (one-run)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.translation_quality import (
    apply_translation_quality_pass,
    extract_abbreviations,
    extract_preserved_tokens,
    validate_raw_mt,
)
from engines.translation_trace import SegmentTrace, TranslationTraceLog


def test_raw_mt_validation() -> None:
    assert "raw_equals_whisper" in validate_raw_mt(
        "Hello world", "Hello world", source_lang="en", target_lang="ru"
    )
    assert not validate_raw_mt("Hello", "Привет", source_lang="en", target_lang="ru")


def test_abbreviations() -> None:
    src = "John Smith Jr. studied at USC in the U.S."
    abb = extract_abbreviations(src)
    assert any("Jr" in a for a in abb)
    assert any("USC" in a for a in abb)
    toks = extract_preserved_tokens(src)
    assert "John" in toks or "Smith" in toks


def test_preserve_jr_usc() -> None:
    src = ["John Smith Jr. went to USC"]
    tr = ["Джон Смит пошёл в университет"]
    out = apply_translation_quality_pass(src, tr)
    assert out[0] == tr[0]
    assert "USC" not in out[0] or "usc" in out[0].lower()


def test_discourse_markers_excluded() -> None:
    from engines.translation_quality import extract_proper_nouns, preserve_preserved_tokens

    src = "Like, why did you change the name?"
    tr = "Як, чому ти змінив назву?"
    assert "Like" not in extract_proper_nouns(src)
    assert preserve_preserved_tokens(src, tr) == tr


def test_trace_stages() -> None:
    tr = SegmentTrace(
        index=0,
        whisper="Hello",
        raw_mt="Привет",
        naturalized="Привет!",
        final="Привет",
        source_lang="en",
        target_lang="ru",
    )
    line = tr.to_log_line()
    assert "raw_mt=" in line and "naturalized=" in line
    assert "idx=0" in line


def test_trace_log_flush(tmp_path: Path | None = None) -> None:
    app = tmp_path or ROOT
    log = TranslationTraceLog(app, task_id="test")
    log.upsert_from_audit(
        {
            "index": 0,
            "whisper_text": "Hi",
            "raw_translation": "Привет",
            "naturalized_text": "Привет!",
            "final_text": "Привет!",
            "source_lang": "en",
            "target_lang": "ru",
            "engine": "mock",
            "route": "direct",
        }
    )
    path = log.flush(phase="test")
    assert Path(path).is_file()
    body = Path(path).read_text(encoding="utf-8")
    assert "raw_mt=" in body and "naturalized=" in body


def test_semantic_calque_detection() -> None:
    from engines.semantic_translation import (
        apply_semantic_polish_line,
        detect_semantic_issues,
    )

    issues = detect_semantic_issues(
        "It doesn't make sense.",
        "Это не делает смысл.",
        source_lang="en",
        target_lang="ru",
    )
    assert any(i.get("code") == "literal_construction" for i in issues)

    fixed = apply_semantic_polish_line("Это не делает смысл.", target_lang="ru")
    assert "имеет смысл" in fixed.lower()

    idiom_issues = detect_semantic_issues(
        "It's a piece of cake.",
        "Это кусок торта.",
        source_lang="en",
        target_lang="ru",
    )
    assert any(i.get("code") in ("literal_construction", "idiom") for i in idiom_issues)


def main() -> int:
    test_raw_mt_validation()
    test_abbreviations()
    test_preserve_jr_usc()
    test_discourse_markers_excluded()
    test_trace_stages()
    test_trace_log_flush()
    test_semantic_calque_detection()
    print("translation pipeline invariants: OK")
    print("Trace log:", ROOT / "output" / "dev" / "translation_trace.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
