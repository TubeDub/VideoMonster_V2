# -*- coding: utf-8 -*-
"""Regression: 555.zip phrase-loop meaning_collapse must heal without LLM."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.mt.cross_script_guard import (  # noqa: E402
    deflate_phrase_loop,
    has_phrase_loop,
    meaning_collapse,
)
from engines.pipeline_language_gate import (  # noqa: E402
    heal_phrase_loops_in_segments,
    salvage_collapsed_segment_text,
    validate_segments_target_language,
)

UK_LOOP = (
    "Тепер ви вагітні, ви в будинку, ви в будинку, ви в будинку, "
    "ви в будинку, ви в будинку, ви в будинку, ви в будинку."
)

SEG8 = (
    "Тож за два тижні до того. у той момент, у той момент, у той момент, "
    "у той момент, у той момент, у той момент, у той момент, коли́ Джордж "
    "повертав. а по́тім щось трапилося"
)
SRC8 = (
    "Two years later, George Jr., who by this point was fully recovered "
    "from his injuries, stood at the finish line at a race"
)

SEG13 = (
    "Джордж-молодший підійшов до подіуму. щоб сфотографувати водія-переможця. "
    "але́ у той момент, у той момент, у той момент, у той момент, у той момент, "
    "у той момент, коли́ ві́н підійшов до нього. цей чоловік середнього віку "
    "підійшов до нього та про́сто запитав Джорджа-молодшого про його́ фотографію. "
    "а по́тім у якийсь момент чоловік фактично офіційно представився як "
    "Хаскелл Векслер і сказав"
)
SRC13 = (
    "Now, George Jr. walked over to the podium to take some photos of the "
    "winning driver but as he walked over there, this middle-aged man walked "
    "up to him and just asked George Jr. about his photography and then at "
    "some point the man actually formally introduced himself as Haskell "
    "Wexler and said"
)


def test_deflate_argos_loop():
    assert has_phrase_loop(UK_LOOP)
    fixed = deflate_phrase_loop(UK_LOOP)
    assert not has_phrase_loop(fixed)
    assert "ви в будинку" in fixed.lower()
    assert fixed.lower().count("ви в будинку") == 1


def test_deflate_555_segments():
    for text in (SEG8, SEG13):
        assert has_phrase_loop(text)
        fixed = deflate_phrase_loop(text)
        assert not has_phrase_loop(fixed)
        assert "у той момент" in fixed.lower()
        # One kept occurrence (may appear once)
        assert fixed.lower().count("у той момент") == 1


def test_meaning_collapse_clears_after_deflate():
    assert meaning_collapse(SRC8, SEG8, source_lang="en", target_lang="uk")
    fixed8 = deflate_phrase_loop(SEG8)
    assert meaning_collapse(SRC8, fixed8, source_lang="en", target_lang="uk") is None

    assert meaning_collapse(SRC13, SEG13, source_lang="en", target_lang="uk")
    fixed13 = deflate_phrase_loop(SEG13)
    assert meaning_collapse(SRC13, fixed13, source_lang="en", target_lang="uk") is None


def test_salvage_deflates_phrase_loop():
    fixed, method = salvage_collapsed_segment_text(
        text=SEG8,
        original=SRC8,
        target_lang="uk",
        source_lang="en",
    )
    assert fixed
    assert "deflate" in method
    assert not has_phrase_loop(fixed)


def test_heal_and_validate_555_snapshot():
    zip_path = Path(r"c:\Users\serhii\Desktop\555.zip")
    if not zip_path.is_file():
        # Fallback to synthetic rows when zip is absent
        rows = [
            {"text": SEG8, "plain_text": SEG8, "segment_id": "8"},
            {"text": SEG13, "plain_text": SEG13, "segment_id": "13"},
        ]
        src = [SRC8, SRC13]
    else:
        with zipfile.ZipFile(zip_path) as zf:
            rows = json.loads(zf.read("snapshot_after.json"))
        # Reconstruct sources from SegmentTrace is unavailable; use empty → raw deflate
        src = [""] * len(rows)
        # Prefer known sources for the two looped indices when present
        if len(rows) > 13:
            src[8] = SRC8
            src[13] = SRC13

    before = validate_segments_target_language(
        rows, source_segments=src, target_lang="uk", source_lang="en"
    )
    assert any(
        i.get("code") in ("meaning_collapse", "phrase_loop")
        or i.get("category") in ("meaning_collapse", "phrase_loop")
        for i in before
    ), before
    # Must never be reported as Language Mismatch when text is Ukrainian.
    assert all(i.get("category") != "language_mismatch" for i in before), before

    healed = heal_phrase_loops_in_segments(
        rows, source_segments=src, target_lang="uk", source_lang="en"
    )
    assert healed
    after = validate_segments_target_language(
        rows,
        source_segments=src,
        target_lang="uk",
        source_lang="en",
        hard_only=True,
    )
    assert after == [], after


def main() -> int:
    test_deflate_argos_loop()
    test_deflate_555_segments()
    test_meaning_collapse_clears_after_deflate()
    test_salvage_deflates_phrase_loop()
    test_heal_and_validate_555_snapshot()
    print("phrase_loop_deflate OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
