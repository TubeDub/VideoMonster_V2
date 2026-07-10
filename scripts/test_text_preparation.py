"""Text preparation unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_punctuation():
    from engines.text_preparation import prepare_text_for_tts

    out, meta = prepare_text_for_tts("Привіт , світ!", lang="uk")
    assert "," in out
    assert meta["changed"] or out == "Привіт , світ!"


def test_abbreviation_ru():
    from engines.text_preparation import prepare_text_for_tts

    src = "\u042d\u0442\u043e \u0442.\u0434. \u0438 \u0442.\u043f."
    out, meta = prepare_text_for_tts(src, lang="ru")
    assert "\u0442\u0430\u043a \u0434\u0430\u043b\u0435\u0435" in out
    assert "\u0442\u043e\u043c\u0443 \u043f\u043e\u0434\u043e\u0431\u043d\u043e\u0435" in out
    assert meta["changed"]


def test_batch():
    from engines.text_preparation import prepare_segments_for_tts

    segs, meta = prepare_segments_for_tts(["Hello 100%", "Test"], lang="en")
    assert len(segs) == 2
    assert meta["segments"] == 2


def main() -> int:
    test_punctuation()
    test_abbreviation_ru()
    test_batch()
    print("text preparation tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
