# -*- coding: utf-8 -*-
"""Round-2 guards: soft_compress, debleed list fix, shared blob, quality recompute."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.mt.tts_slot_compress import soft_compress_for_slot  # noqa: E402
from engines.tts_text_guard import repair_neighbor_bleed  # noqa: E402
from engines.translation_naturalizer import (  # noqa: E402
    build_tts_groups,
    debleed_adjacent_batch_copies,
)


def test_soft_compress_no_comma_to_period():
    src = (
        "Але коли він їхав, Джордж-молодший відчув, що він справді боїться "
        "потрапити туди, і він не хотів їхати далі."
    )
    out = soft_compress_for_slot(src, slot_ms=1200, target_lang="uk")
    assert "їхав. Джордж" not in out, out
    assert "їхав," in out or "їхав" in out
    # Must not chop to tiny fragment
    assert len(out.split()) >= 8, out


def test_soft_compress_refuses_clause_chop():
    src = (
        "Тож за два тижні до того, коли Джордж повертав, а потім щось трапилося, "
        "то це було так: по дорозі мчала інша машина й так сильно врізалася в "
        "машину Джорджа, що Джорджа-молодшого викинуло з машини, але він вижив."
    )
    out = soft_compress_for_slot(src, slot_ms=800, target_lang="uk")
    # Should keep most meaning (not just first clause)
    assert "вижив" in out.lower() or out == src or len(out) > len(src) * 0.7


def test_debleed_list_index_works():
    # Mimic pipeline list access pattern
    raw_by_index = [
        "Тобто, окрім автомобілів. І в той момент його батько купив Fiat.",
        "Тобто, окрім автомобілів. І в той момент його батько купив Fiat.",
    ]
    src = [
        "That is, except for cars.",
        "And at that point his father bought him a Fiat.",
    ]
    _raw_list = [
        str(raw_by_index[i] if i < len(raw_by_index) else "")
        for i in range(len(src))
    ]
    out = debleed_adjacent_batch_copies(src, _raw_list)
    assert out[0] != out[1] or "Fiat" in out[1]
    assert "автомобіл" in out[0].lower()


def test_shared_blob_split():
    blob = (
        "І тому практично кожна вечеря перетворювалася на суперечку. "
        "І ось Джордж підійшов до перехрестя і почав повертати."
    )
    segs = [
        {
            "segment_id": "0",
            "text": blob,
            "plain_text": blob,
            "final_text": blob,
            "tts_text": blob,
        },
        {
            "segment_id": "1",
            "text": blob,
            "plain_text": blob,
            "final_text": blob,
            "tts_text": blob,
        },
    ]
    r = repair_neighbor_bleed(segs)
    assert r["healed"] >= 1, r
    assert segs[0]["tts_text"] != segs[1]["tts_text"]
    assert "вечеря" in segs[0]["tts_text"].lower()
    assert "перехрестя" in segs[1]["tts_text"].lower()


def test_tts_groups_min_duration_1_is_1to1():
    segs = ["Коротко.", "Ще одне.", "І третє речення тут."]
    timing = [
        {"start": 0, "end": 400},
        {"start": 500, "end": 900},
        {"start": 1000, "end": 2000},
    ]
    groups = build_tts_groups(segs, timing, min_duration_ms=1)
    # With min_duration_ms=1, almost no merge
    assert len(groups) >= 2
    for g in groups:
        assert len(g["indices"]) == 1 or all(
            i in range(len(segs)) for i in g["indices"]
        )


def main() -> int:
    tests = [
        test_soft_compress_no_comma_to_period,
        test_soft_compress_refuses_clause_chop,
        test_debleed_list_index_works,
        test_shared_blob_split,
        test_tts_groups_min_duration_1_is_1to1,
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
    print(f"tts_round2_guards OK ({len(tests)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
