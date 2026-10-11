# -*- coding: utf-8 -*-
"""Stage 42 — UK bleed, Hey≠Гей, 1 speaker/segment, Simple UK mute (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_neighbor_bleed_blocked_each_keeps_own():
    from engines.repetition_guard import (
        is_tts_neighbor_bleed,
        sanitize_neighbor_bleed,
    )

    seg1 = "Хей, чувак, дивись. Долар? Ти серйозно?"
    seg2 = "Долар? Ти серйозно?"
    assert is_tts_neighbor_bleed(seg1, seg2)
    blob = "Хей, чувак, дивись. Долар? Ти серйозно?"
    out, n = sanitize_neighbor_bleed(
        [blob, blob],
        raw_mts=["Хей, чувак, дивись.", "Долар? Ти серйозно?"],
        naturalized=["Хей, чувак, дивись.", "Долар? Ти серйозно?"],
        sources=["Hey man, look.", "A dollar? Are you serious?"],
    )
    assert n >= 1
    assert "серйозно" not in out[0].lower()
    assert "чувак" not in out[1].lower()
    assert "дивись" in out[0].lower() or "чувак" in out[0].lower()
    assert "долар" in out[1].lower() or "серйозно" in out[1].lower()


def test_naturalized_cannot_copy_all_of_neighbor():
    from engines.repetition_guard import refuse_naturalized_inheritance

    n1 = "Хей, чувак, дивись сюди уважно."
    n2 = "Хей, чувак, дивись сюди уважно."
    own = "Долар? Ти серйозно?"
    out = refuse_naturalized_inheritance(n2, n1, own)
    assert out == own
    assert out != n1


def test_mt_cache_different_original_invalidates(tmp_path):
    from engines.mt_cache import (
        cached_payload_matches_source,
        lookup_mt_cache,
        store_mt_cache,
    )

    store_mt_cache(
        "Hey man look at this",
        "Хей, чувак, дивись",
        "en",
        "uk",
        cache_dir=tmp_path,
    )
    assert lookup_mt_cache("A dollar? Are you serious?", "en", "uk", cache_dir=tmp_path) is None
    payload = {
        "source": "Hey man look at this",
        "source_hash": "deadbeef",
        "translated": "Хей, чувак, дивись",
    }
    assert cached_payload_matches_source("A dollar? Are you serious?", payload) is False
    assert cached_payload_matches_source("Hey man look at this", {"source": "Hey man look at this"}) is True


def test_hey_vocative_not_gey():
    from engines.mt.glossary_en_uk import (
        finalize_mt_text,
        rewrite_hey_vocative_uk,
    )

    hey_man = rewrite_hey_vocative_uk("Гей, чувак")
    assert "Гей," not in hey_man
    assert "Хей" in hey_man
    big = rewrite_hey_vocative_uk("Гей, мій здоровяк")
    assert "Гей," not in big
    assert "здоров'яче" in big or "Давай" in big
    done = finalize_mt_text("en", "uk", "Гей, чувак")
    assert "Гей," not in done


def test_ru_i_and_male_vtratyv():
    from engines.mt.glossary_en_uk import apply_uk_gender_lock, apply_uk_ru_conjunction_i

    out = apply_uk_ru_conjunction_i("Да, и дивись")
    assert " и " not in f" {out} "
    assert "і" in out
    male = apply_uk_gender_lock("Я втратила ключі", gender="male")
    assert "втратив" in male
    assert "втратила" not in male


def test_shut_up_surprise_not_zatknys():
    from engines.mt.glossary_en_uk import apply_surprise_shut_up

    out = apply_surprise_shut_up("Заткнись", source="Shut up!")
    assert "Заткнись" not in out
    assert "Та ну" in out or "Серйозно" in out
    long = apply_surprise_shut_up(
        "Замовкни. Ти це вже цілий рік робиш?",
        source="Shut up. You've been doing this for a whole year now?",
    )
    assert "Замовкни" not in long
    assert "Та ну" in long or "Серйозно" in long


def test_eighteen_nineteen_cents_vs_any_guy_bleed():
    from engines.repetition_guard import sanitize_neighbor_bleed

    srcs = [
        "Sometimes I do it for as low as like ten cents.",
        "Just any guy.",
    ]
    blob = "Іноді я роблю це за 10 центів. Просто будь-який хлопець."
    out, n = sanitize_neighbor_bleed(
        [blob, blob + "."],
        raw_mts=[blob, blob],
        naturalized=[blob, blob],
        sources=srcs,
    )
    assert n >= 1
    assert "хлопець" not in out[0].lower()
    assert "цент" not in out[1].lower()


def test_money_3000_restored():
    from engines.mt.glossary_en_uk import restore_dropped_source_entities

    out = restore_dropped_source_entities(
        "It's just $3,000. I really hope that can help.",
        "Я дуже сподіваюся, що це допоможе.",
    )
    assert "3000" in out or "три тисячі" in out
    assert "долар" in out.lower()
    spaced = restore_dropped_source_entities(
        "It's just $3, 000.",
        "Я дуже сподіваюся.",
    )
    assert "3000" in spaced or "три тисячі" in spaced


def test_en_tokens_stripped_from_tts():
    from engines.mt.glossary_en_uk import strip_en_tokens_for_uk_tts

    out = strip_en_tokens_for_uk_tts("Yeah Are you serious? Honestly Before Jonathan left.")
    low = out.lower()
    assert "yeah" not in low
    assert "are" not in low.split()
    assert "honestly" not in low
    assert "before" not in low
    assert "jonathan" not in low
    assert "джонатан" in low


def test_two_speaker_original_splits():
    from engines.diarization import SPEAKER_A, SPEAKER_B, split_mixed_speaker_segments

    seg = {
        "segment_id": "parent-mix",
        "original_text": "Hey man, look at this. A dollar? Are you serious?",
        "text": "Hey man, look at this. A dollar? Are you serious?",
        "start_ms": 0,
        "end_ms": 4000,
        "words": [
            {"word": "Hey", "start": 0, "end": 200, "speaker": SPEAKER_A},
            {"word": "man", "start": 200, "end": 400, "speaker": SPEAKER_A},
            {"word": "look", "start": 400, "end": 600, "speaker": SPEAKER_A},
            {"word": "at", "start": 600, "end": 700, "speaker": SPEAKER_A},
            {"word": "this.", "start": 700, "end": 900, "speaker": SPEAKER_A},
            {"word": "A", "start": 1400, "end": 1500, "speaker": SPEAKER_B},
            {"word": "dollar?", "start": 1500, "end": 1800, "speaker": SPEAKER_B},
            {"word": "Are", "start": 1800, "end": 2000, "speaker": SPEAKER_B},
            {"word": "you", "start": 2000, "end": 2200, "speaker": SPEAKER_B},
            {"word": "serious?", "start": 2200, "end": 2600, "speaker": SPEAKER_B},
        ],
    }
    out = split_mixed_speaker_segments([seg])
    live = [s for s in out if not s.get("archived")]
    assert len(live) == 2
    assert live[0]["speaker_id"] != live[1]["speaker_id"]
    joined = " ".join(str(s.get("original_text") or s.get("text") or "") for s in live)
    assert "Hey man" in joined or "look" in joined.lower()
    assert "dollar" in joined.lower() or "Dollar" in joined


def test_simple_uk_mux_source_vocal_muted():
    from engines.simple_dub_pipeline import (
        SIMPLE_UK_SOURCE_DUCK_DB,
        SIMPLE_UK_SOURCE_UNDERLAY,
        apply_simple_uk_source_underlay,
    )

    info = {"simple_pipeline": True, "happy_path": True, "target_lang": "uk"}
    mv = apply_simple_uk_source_underlay(
        info,
        {"original_volume": 0.20, "dub_volume": 1.0, "mix_mode": "custom"},
    )
    assert float(SIMPLE_UK_SOURCE_UNDERLAY) == 0.0
    assert float(mv["original_volume"]) == 0.0
    assert float(mv["original_volume"]) <= 10 ** (-30 / 20) + 1e-6
    assert float(mv.get("ducking_db") or 0) <= SIMPLE_UK_SOURCE_DUCK_DB
    assert info.get("source_vocal_muted") is True
    assert (info.get("audio") or {}).get("source_vocal_muted") is True

    other = apply_simple_uk_source_underlay(
        {"simple_pipeline": False, "target_lang": "uk"},
        {"original_volume": 0.22, "dub_volume": 1.0},
    )
    assert abs(float(other["original_volume"]) - 0.22) < 1e-6


def test_simple_uk_user_source_volume_50_respected():
    from engines.simple_dub_pipeline import apply_simple_uk_source_underlay

    info = {"simple_pipeline": True, "happy_path": True, "target_lang": "uk"}
    mv = apply_simple_uk_source_underlay(
        info,
        {"original_volume": 0.0, "dub_volume": 1.0, "mix_mode": "full_dub"},
        explicit_original=0.5,
    )
    assert abs(float(mv["original_volume"]) - 0.5) < 1e-6
    assert mv.get("mix_mode") == "custom"
    assert mv.get("ducking_enabled") is False
    assert info.get("source_vocal_muted") is False

    info2 = {
        "simple_pipeline": True,
        "happy_path": True,
        "target_lang": "uk",
        "source_volume": 0.5,
    }
    mv2 = apply_simple_uk_source_underlay(
        info2, {"original_volume": 0.0, "dub_volume": 1.0, "mix_mode": "full_dub"}
    )
    assert abs(float(mv2["original_volume"]) - 0.5) < 1e-6

    muted = apply_simple_uk_source_underlay(
        {"simple_pipeline": True, "happy_path": True, "target_lang": "uk"},
        {"original_volume": 0.0, "dub_volume": 1.0, "mix_mode": "full_dub"},
        explicit_original=0.0,
    )
    assert float(muted["original_volume"]) == 0.0


def test_jonathan_to_dzhonatan():
    from engines.mt.glossary_en_uk import finalize_mt_text, strip_en_tokens_for_uk_tts

    assert "Джонатан" in strip_en_tokens_for_uk_tts("Jonathan, you seem smart.")
    assert "Jonathan" not in finalize_mt_text("en", "uk", "Так. Jonathan, ти розумний.")


def test_stage42_review_flags_do_not_require_live_mutate():
    from engines.repetition_guard import stamp_stage42_segment_flags

    seg = {
        "speaker_id": "SPEAKER_A",
        "final_tts_text": "Хей, чувак, дивись.",
    }
    flags = stamp_stage42_segment_flags(
        seg,
        previous_text="",
        next_text="Долар? Ти серйозно?",
    )
    assert flags["speaker_id"] == "SPEAKER_A"
    assert flags["has_gey_vocative"] is False
    assert flags["gender_form"] == "ok"
    assert "final_tts_text" in flags
