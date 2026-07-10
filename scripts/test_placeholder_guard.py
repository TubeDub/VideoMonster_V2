"""Placeholder guard and quality score regression tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_detect_legacy_leaks():
    from engines.placeholder_guard import detect_placeholder_leaks, has_placeholder_leak

    assert has_placeholder_leak("PERSON_GJR_1 був розумний")
    assert has_placeholder_leak("PERSON GJR 1 був розумний")
    assert has_placeholder_leak("ОСОБА_ГЖР_1 був розумний")
    assert not has_placeholder_leak("Джордж молодший був розумний")


def test_fuzzy_restore():
    from engines.naturalizer_v2.entity_tokens import restore_entities

    token_map = {"PERSON_GJR_1": "George Jr."}
    text = "PERSON GJR 1 був дуже розумний"
    out, labels = restore_entities(
        text,
        token_map,
        original="George Jr. was very smart",
        tgt_lang="uk",
    )
    assert "PERSON" not in out
    assert "GJR" not in out
    assert labels


def test_opaque_tokens_not_leaked():
    from engines.naturalizer_v2.entity_tokens import mask_entities, restore_entities

    src = "George Jr. went to USC"
    masked, tmap = mask_entities(src)
    assert "PERSON_" not in masked
    assert "ORG_" not in masked
    assert "[##" in masked
    out, _ = restore_entities(masked, tmap, original=src, tgt_lang="uk")
    assert "[##" not in out
    assert "⟦" not in out


def test_nuclear_restore_bracket_storm():
    from engines.placeholder_guard import has_mt_garbage, nuclear_restore_placeholders

    damaged = "Але, як він був водінням, " + ", ".join(["⟦"] * 20)
    out, notes = nuclear_restore_placeholders(
        damaged,
        {"[##1##]": "George Jr."},
        replace_fn=lambda e: "Джордж-молодший",
    )
    assert not has_mt_garbage(out)
    assert out.count("Джордж-молодший") == 1
    assert notes


def test_bcast_token_mask_restore():
    from engines.naturalizer_v2.entity_tokens import mask_entities, restore_entities

    src = "George Jr. was smart"
    masked, tmap = mask_entities(src)
    assert any(k.startswith("[##") for k in tmap)
    corrupted = masked.replace(list(tmap.keys())[0], "[## 1 ##]")
    out, _ = restore_entities(corrupted, tmap, original=src, tgt_lang="uk")
    assert "[##" not in out
    assert "[#" not in out
    assert "⟦" not in out


def test_mt_damaged_hash_tokens():
    """Argos/Deep often corrupt [##1##] → [#1#] or [#1##]."""
    from engines.naturalizer_v2.entity_tokens import mask_entities, restore_entities
    from engines.placeholder_guard import has_mt_garbage, reset_segment_tokens

    reset_segment_tokens()
    src = "An 18-year-old boy named George Jr. drove through his hometown."
    _, tmap = mask_entities(src)
    mt = "18-річний хлопчик названий [#1#] поїхав через рідний міст."
    assert has_mt_garbage(mt)
    out, labels = restore_entities(mt, tmap, original=src, tgt_lang="uk")
    assert "[#1#]" not in out
    assert "Джордж-молодший" in out
    assert labels

    src2 = "So George Jr. was smart. So George Jr. had decided."
    reset_segment_tokens()
    _, tmap2 = mask_entities(src2)
    mt2 = "Так [#1##] був розумний. Так [#1##] вирішив."
    out2, _ = restore_entities(mt2, tmap2, original=src2, tgt_lang="uk")
    assert out2.count("Джордж-молодший") == 2
    assert "[#" not in out2


def test_global_unique_token_ids():
    from engines.naturalizer_v2.entity_tokens import mask_segments

    segs = ["George Jr. drove home.", "The Fiat was red.", "George Lucas created Star Wars."]
    _, maps = mask_segments(segs)
    tokens = [list(m.keys()) for m in maps if m]
    flat = [t for m in maps for t in m]
    assert len(flat) == len(set(flat)), "token ids must be globally unique"


def test_sanitize_george_lucas_for_jr():
    from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

    src = "George Jr. drove through his hometown."
    bad = "18-річний хлопчик названий George Lucas поїхав додому."
    out = sanitize_wrong_entity_substitutions(bad, original=src, tgt_lang="uk")
    assert "George Lucas" not in out
    assert "Джордж-молодший" in out


def test_resolve_no_global_id_collision():
    from engines.naturalizer_v2.entity_tokens import mask_segments
    from engines.placeholder_guard import resolve_token_map_for_text

    segs = ["George Jr. went home.", "Fiat is small."]
    _, maps = mask_segments(segs)
    tok = next(iter(maps[0]))
    emap = resolve_token_map_for_text(f"hello {tok} world", [maps[0]])
    entities = set(emap.values())
    assert "George Jr." in entities or "George Jr" in entities
    assert "Fiat" not in entities
    # Id 1 must not pick Fiat from segment 2 when resolving damaged token
    emap2 = resolve_token_map_for_text("[#1#] world", maps)
    assert emap2.get("[#1#]") in ("George Jr.", "George Jr")


def test_grouped_segment_token_restore():
    """Token from seg N may land in seg N+1 after group MT split."""
    from engines.naturalizer_v2.entity_tokens import mask_segments, restore_entities
    from engines.placeholder_guard import has_mt_garbage, resolve_token_map_for_text

    segs = [
        "And his father bought him a Fiat, but his father gave him the Fiat,",
        "he just didn't get his son's obsession with cars.",
    ]
    masked, maps = mask_segments(segs)
    assert maps[0]
    mt_split = "[##1##], він просто не зрозумів одержимість свого сина автомобілями."
    assert has_mt_garbage(mt_split)
    emap = resolve_token_map_for_text(mt_split, maps)
    assert emap
    out, _ = restore_entities(
        mt_split,
        emap,
        original=" ".join(segs),
        tgt_lang="uk",
    )
    assert "[##" not in out
    assert "Fiat" in out or "fiat" in out.lower()


def test_multi_entity_loose_tokens():
    from engines.naturalizer_v2.entity_tokens import mask_entities, restore_entities

    src = (
        "George Jr. had applied to the University of Southern California, "
        "and met Haskell Wexler in Hollywood."
    )
    _, tmap = mask_entities(src)
    mt = "насправді, [#2#] приймав участь у програмі [#1#], а [#3#] в [#4#]."
    out, _ = restore_entities(mt, tmap, original=src, tgt_lang="uk")
    assert "[#" not in out
    assert "Джордж-молодший" in out or "Джордж" in out


def test_quality_score_penalizes_placeholders():
    from engines.translation_quality_score import compute_quality_score

    score, metrics = compute_quality_score(
        "George Jr. was smart",
        "PERSON GJR 1 був розумний",
        src_lang="en",
        tgt_lang="uk",
    )
    assert metrics["placeholder_leak_count"] > 0
    assert score == 0.0


def test_damaged_opaque_detect_and_restore():
    from engines.placeholder_guard import (
        detect_placeholder_leaks,
        restore_placeholders_fuzzy,
    )

    damaged = "18 \u27e6b9f162 \u27e7 boy"
    assert detect_placeholder_leaks(damaged)
    canon = "\u27e6b9f162\u27e7"
    out, labels = restore_placeholders_fuzzy(
        damaged,
        {canon: "George Jr."},
        replace_fn=lambda e: "George Jr.",
    )
    assert "\u27e6" not in out
    assert labels


def test_manager_rejects_leaky_candidate():
    from engines.translation_manager import _placeholder_penalty

    p = _placeholder_penalty("George Jr.", "PERSON GJR 1 text")
    assert p >= 30.0


def test_collapse_duplicates():
    from engines.placeholder_guard import collapse_repeated_phrases

    t = "Але Джордж молодшийДжордж молодшийДжордж молодший був"
    out = collapse_repeated_phrases(t, ["Джордж молодший"])
    assert out.count("Джордж молодший") == 1


def test_cjk_sweep():
    from engines.placeholder_guard import sweep_cjk_clusters

    t = "Так 浜u 涓 蹇 вирішив"
    out, notes = sweep_cjk_clusters(t, ["Джордж молодший"])
    assert "\u4e00" not in out or notes


def main() -> int:
    test_detect_legacy_leaks()
    test_fuzzy_restore()
    test_opaque_tokens_not_leaked()
    test_nuclear_restore_bracket_storm()
    test_bcast_token_mask_restore()
    test_mt_damaged_hash_tokens()
    test_global_unique_token_ids()
    test_sanitize_george_lucas_for_jr()
    test_resolve_no_global_id_collision()
    test_grouped_segment_token_restore()
    test_multi_entity_loose_tokens()
    test_quality_score_penalizes_placeholders()
    test_damaged_opaque_detect_and_restore()
    test_collapse_duplicates()
    test_cjk_sweep()
    test_manager_rejects_leaky_candidate()
    print("placeholder guard tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
