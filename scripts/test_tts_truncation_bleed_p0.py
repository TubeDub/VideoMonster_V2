# -*- coding: utf-8 -*-
"""P0 regression: compact phrases, phrase loops, neighbor bleed, no bare infinitive."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.semantic_meaning import apply_compact_phrases  # noqa: E402
from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop  # noqa: E402
from engines.tts_text_guard import (  # noqa: E402
    prepare_segment_text_for_tts,
    repair_neighbor_bleed,
)
from engines.translation_validation import stamp_authoritative_final_text  # noqa: E402


def test_compact_no_bare_infinitive():
    src = (
        "Але коли він їхав, Джордж-молодший не міг не відчути, "
        "що він справді боїться потрапити туди."
    )
    out = apply_compact_phrases(src, target_lang="uk")
    assert "не міг не відчути" not in out.lower() or "відчув" in out.lower()
    assert not (
        "відчути" in out.lower() and "відчув" not in out.lower()
    ), f"bare infinitive left: {out}"
    assert "боїться" in out.lower()
    print("compact:", out)


def test_phrase_loop_deflate():
    loop = (
        "Тож за два тижні до того. у той момент, у той момент, у той момент, "
        "у той момент, у той момент, у той момент, у той момент, коли Джордж повертав."
    )
    assert has_phrase_loop(loop)
    fixed = deflate_phrase_loop(loop)
    assert not has_phrase_loop(fixed)
    assert fixed.lower().count("у той момент") == 1


def test_neighbor_bleed_restore():
    a = "І тому практично кожна вечеря перетворювалася на суперечку між батьком і сином."
    b_blob = (
        a
        + " І ось Джордж підійшов до перехрестя і почав повертати."
    )
    segs = [
        {
            "segment_id": "a",
            "final_text": a,
            "text": a,
            "plain_text": a,
            "tts_text": a,
        },
        {
            "segment_id": "b",
            "final_text": b_blob,  # shared blob wrongly assigned
            "text": b_blob,
            "plain_text": b_blob,
            "tts_text": b_blob,
        },
    ]
    # Simulate: seg1 spoken text starts with seg0
    segs[1]["tts_text"] = b_blob
    segs[1]["text"] = "І ось Джордж підійшов до перехрестя і почав повертати."
    segs[1]["plain_text"] = segs[1]["text"]
    segs[1]["final_text"] = segs[1]["text"]
    # But tts still has prefix bleed
    segs[1]["tts_text"] = a + " " + segs[1]["text"]
    result = repair_neighbor_bleed(segs)
    assert result["healed"] >= 1, result
    spoken = segs[1]["tts_text"]
    assert not spoken.startswith(a[:40]), spoken


def test_stamp_sets_tts_text():
    seg: dict = {"translated_text": "RAW MT KEEP"}
    stamp_authoritative_final_text(
        seg,
        "Тож за два тижні. у той момент, у той момент, у той момент, "
        "у той момент, коли Джордж повертав.",
    )
    assert seg["tts_text"] == seg["text"]
    assert not has_phrase_loop(seg["tts_text"])
    assert seg["translated_text"] == "RAW MT KEEP"


def test_prepare_rejects_bare_after_subject():
    seg = {
        "text": "Джордж-молодший не міг не відчути, що він боїться.",
        "final_text": "Джордж-молодший не міг не відчути, що він боїться.",
    }
    prepare_segment_text_for_tts(seg)
    assert "молодший відчути" not in seg["tts_text"].lower()
    assert "боїться" in seg["tts_text"].lower()


def main() -> int:
    tests = [
        test_compact_no_bare_infinitive,
        test_phrase_loop_deflate,
        test_neighbor_bleed_restore,
        test_stamp_sets_tts_text,
        test_prepare_rejects_bare_after_subject,
    ]
    failed = []
    for i, fn in enumerate(tests, 1):
        try:
            fn()
            print(f"[{i}/{len(tests)}] OK {fn.__name__}")
        except Exception as exc:
            print(f"[{i}/{len(tests)}] FAIL {fn.__name__}: {exc}")
            failed.append(fn.__name__)
    if failed:
        print("FAILED", failed)
        return 1
    print(f"tts_truncation_bleed_p0 OK ({len(tests)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
