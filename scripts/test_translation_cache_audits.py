"""Cache-hit audit synthesis — Rule 6: same review shape as full pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.translation_quality_log import synthesize_audits_from_segments
from engines.translation_review import build_translation_review


def test_cache_audits_have_all_stages() -> None:
    src = ["Hello world", "John went home"]
    tr = ["Привет мир", "Джон пошёл домой"]
    audits = synthesize_audits_from_segments(src, tr, "en", "ru", engine="cache")
    assert len(audits) == 2
    for a in audits:
        assert a.whisper_text
        assert a.raw_translation
        assert a.naturalized_text
        assert a.final_text
        assert a.quality_pass_before
        assert a.quality_pass_after
        assert a.semantic_text
        assert a.engine == "cache"
        assert a.whisper_len > 0
        assert a.raw_len > 0


def test_cache_review_build() -> None:
    src = ["Hello"]
    tr = ["Привет"]
    audits = synthesize_audits_from_segments(src, tr, "en", "ru", engine="cache")
    info = {
        "source_segments": src,
        "translation_audits": [a.__dict__ for a in audits],
        "source_lang": "en",
        "target_lang": "ru",
    }
    review = build_translation_review(info)
    assert review.get("segments")
    assert len(review["segments"]) == 1
    seg = review["segments"][0]
    assert seg.get("original")
    assert seg.get("final_text")


def main() -> int:
    test_cache_audits_have_all_stages()
    test_cache_review_build()
    print("translation cache audits: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
