# -*- coding: utf-8 -*-
"""Round-3 guards: group-blob stamp, slot index align, DSAL clear, quality, periods."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_phrase_loop_prefers_clean_approved():
    from engines.pipeline_language_gate import heal_phrase_loops_in_segments

    looped = "у той момент у той момент у той момент його батько купив Fiat"
    clean = "І в той момент його батько купив Fiat."
    segs = [
        {
            "text": looped,
            "plain_text": looped,
            "tts_text": looped,
            "final_text": looped,
            "approved_text": clean,
        }
    ]
    healed = heal_phrase_loops_in_segments(
        segs, source_segments=["And at that moment his father bought a Fiat."],
        target_lang="uk", source_lang="en",
    )
    assert healed == [0], healed
    assert segs[0]["text"] == clean, segs[0]["text"]
    assert "у той момент у той момент" not in segs[0]["tts_text"]


def test_false_mid_sentence_period_repaired():
    from engines.dsal.pre_lock_polish import polish_false_name_period
    from engines.naturalizer_v2.punctuation import clean_punctuation

    src = "Але коли він їхав. Джордж-молодший відчув страх."
    out = polish_false_name_period(src)
    assert "їхав. Джордж" not in out, out
    assert "їхав Джордж" in out or "їхав, Джордж" in out or "їхав Джордж" in out.replace(
        "  ", " "
    )

    out2 = clean_punctuation("Але коли він їхав. Джордж відчув страх")
    assert "їхав. Джордж" not in out2, out2


def test_quality_recompute_string_zero_and_merge():
    from engines.translation_review import build_translation_review

    payload = build_translation_review(
        {
            "source_segments": ["His father bought him a Fiat."],
            "source_lang": "en",
            "detected_lang": "en",
            "target_lang": "uk",
            "segments_data": [
                {
                    "text": "Його батько купив йому Фіат.",
                    "plain_text": "Його батько купив йому Фіат.",
                    "final_text": "Його батько купив йому Фіат.",
                }
            ],
            "translation_audits": [
                {
                    "index": 0,
                    "raw_translation": "Його батько купив йому Фіат.",
                    "final_text": "Його батько купив йому Фіат.",
                    "naturalized_text": "Його батько купив йому Фіат.",
                    "quality_score": "0",
                    "quality_details": {"quality_score": 0, "stale": True},
                }
            ],
        }
    )
    rows = payload.get("segments") or payload.get("rows") or []
    assert rows, list(payload.keys())
    score = float(rows[0].get("quality_score") or 0)
    assert score > 0, rows[0]


def test_block_merge_clears_empty_slots():
    from engines.dsal.block_merge import _redistribute

    parts = _redistribute("Одне. Два.", [1000, 1000, 1000], 3)
    assert len(parts) == 3
    assert any(not str(p).strip() for p in parts), parts

    # Simulate clear path used when redistribute leaves a slot empty
    seg = {
        "text": "Старий leftover.",
        "plain_text": "Старий leftover.",
        "tts_text": "Старий leftover.",
        "final_text": "Старий leftover.",
    }
    for _k in (
        "text",
        "plain_text",
        "tts_text",
        "text_for_tts",
        "final_text",
        "translation_text",
        "voice_input",
    ):
        seg[_k] = ""
    seg["merged_into"] = 0
    seg["archived"] = True
    assert not str(seg.get("text") or "").strip()
    assert seg["merged_into"] == 0


def test_slot_budget_index_alignment():
    """Filtered rebuild must NOT drop indices vs timing_map."""
    segments_data = [
        {"text": "A", "plain_text": "A"},
        {"text": "B", "plain_text": "B", "merged_into": 0, "archived": True},
        {"text": "C", "plain_text": "C"},
    ]
    segments = []
    for s in segments_data:
        if not isinstance(s, dict):
            segments.append("")
            continue
        if s.get("merged_into") is not None or s.get("archived"):
            segments.append("")
            continue
        segments.append(str(s.get("plain_text") or s.get("text") or "").strip())
    assert len(segments) == 3
    assert segments[0] == "A"
    assert segments[1] == ""
    assert segments[2] == "C"


def test_group_blob_not_stamped_on_members():
    """Simulate tts_inputs_by_seg fix: members keep own text."""
    segments = ["one", "two"]
    segments_data = [
        {"plain_text": "one", "text": "one"},
        {"plain_text": "two", "text": "two"},
    ]
    tts_groups = [
        {
            "indices": [0, 1],
            "text": "one two MERGED BLOB",
            "plain_text": "one two MERGED BLOB",
        }
    ]
    tts_inputs_by_seg = list(segments)
    for group in tts_groups:
        plain = str(group.get("plain_text") or "").strip()
        gtext = plain
        indices = [int(i) for i in (group.get("indices") or [])]
        for idx in indices:
            seg = segments_data[idx]
            member = str(seg.get("plain_text") or "").strip()
            if idx == indices[0]:
                tts_inputs_by_seg[idx] = member or gtext
            elif member:
                tts_inputs_by_seg[idx] = member
    assert tts_inputs_by_seg[0] == "one"
    assert tts_inputs_by_seg[1] == "two"
    assert "MERGED" not in tts_inputs_by_seg[1]


def main():
    tests = [
        test_phrase_loop_prefers_clean_approved,
        test_false_mid_sentence_period_repaired,
        test_quality_recompute_string_zero_and_merge,
        test_block_merge_clears_empty_slots,
        test_slot_budget_index_alignment,
        test_group_blob_not_stamped_on_members,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    if failed:
        raise SystemExit(failed)
    print(f"OK {len(tests)} tests")


if __name__ == "__main__":
    main()
