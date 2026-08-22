# -*- coding: utf-8 -*-
"""Stage 34 — diagnostic 955dd5ec: PIPELINE_LANG_MIX on leftover RU.

Zip metrics this suite locks:
- OpenDDF stage=Translation RuntimeError
  PIPELINE_LANG_MIX: seg#4 no_remt_or_empty_source ratio=1.0 — refuse skip→silence
- TTS never ran (no census / mix / mux)
- PRE_TTS recovery healed=4 hard_left=0; idx 4 never flagged
- snapshot_after had no original/whisper_text (reissue dropped EN)
- idx4: «Эй, мой здоровяк. Эй, быстро, чувак. Коли ти останнього разу їв?…»
- idx9 leftover «Да ладно. щоб накормить себя?» false-healed
- combining accent «потому́ что» escaped lemma rewrite
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ZIP_IDX4_RU = (
    "Эй, мой здоровяк. Эй, быстро, чувак. "
    "Коли ти останнього разу їв? Ти виглядаєш схвильованим з цього приводу."
)
ZIP_IDX4_EN = (
    "Hey there's my big man. Hey real quick dude. "
    "When was the last time you ate? You seem pretty excited over this."
)
ZIP_IDX9_FALSE_HEAL = "Да ладно. щоб накормить себя?"
ZIP_ACCENT_RU = "Я даже не люблю об этом говорить, потому́ что я молод. Мені лише 25."
UK_CLEAN = "Привіт усім друзям сьогодні, це український текст."


def test_zip_idx4_is_russian_leak():
    from engines.tts_lang_lock import is_uk_tts_text_ok, uk_text_has_russian_leak

    assert uk_text_has_russian_leak(ZIP_IDX4_RU)
    assert not is_uk_tts_text_ok(ZIP_IDX4_RU)
    assert uk_text_has_russian_leak(ZIP_IDX9_FALSE_HEAL)
    assert not is_uk_tts_text_ok(ZIP_IDX9_FALSE_HEAL)
    assert uk_text_has_russian_leak(ZIP_ACCENT_RU)
    assert not uk_text_has_russian_leak(UK_CLEAN)


def test_rewrite_clears_zip_idx4_without_remt():
    from engines.tts_lang_lock import (
        is_uk_tts_text_ok,
        rewrite_russian_leak_for_uk,
        uk_text_has_russian_leak,
    )

    out = rewrite_russian_leak_for_uk(ZIP_IDX4_RU)
    assert "Эй" not in out
    assert "мой" not in out
    assert "быстро" not in out
    assert not uk_text_has_russian_leak(out), out
    assert is_uk_tts_text_ok(out)


def test_fail_loud_empty_source_idx4_does_not_raise():
    """Zip crash: fail_loud + no original → PIPELINE_LANG_MIX. Must rewrite or pad."""
    from engines.tts_lang_lock import enforce_segments_lang_lock

    segs = [{"final_tts_text": ZIP_IDX4_RU, "plain_text": ZIP_IDX4_RU}]
    stats = enforce_segments_lang_lock(
        segs,
        target_lang="uk",
        source_lang="en",
        simple_mode=True,
        fail_loud=True,
        app_dir=ROOT,
    )
    text = str(segs[0].get("final_tts_text") or "")
    if segs[0].get("skip_tts"):
        assert segs[0].get("tts_skip_reason") == "russian_in_uk"
        assert stats["skipped"] >= 1
    else:
        assert "Эй" not in text
        assert "мой" not in text
        assert "быстро" not in text
        assert stats["ok"] >= 1


def test_fail_loud_stubborn_ru_skips_instead_of_pipeline_lang_mix():
    from engines.tts_lang_lock import enforce_segments_lang_lock

    stubborn = "Эй, мой здоровяк. Эй, быстро, чувак."
    segs = [{"final_tts_text": stubborn}]
    with patch(
        "engines.tts_lang_lock.force_remt_segment_no_cache",
        return_value="",
    ), patch(
        "engines.tts_lang_lock.rewrite_russian_leak_for_uk",
        return_value=stubborn,
    ):
        stats = enforce_segments_lang_lock(
            segs,
            target_lang="uk",
            source_lang="en",
            simple_mode=True,
            fail_loud=True,
            app_dir=ROOT,
        )
    assert segs[0].get("skip_tts") is True
    assert segs[0].get("tts_skip_reason") == "russian_in_uk"
    assert stats["skipped"] >= 1
    assert segs[0].get("final_tts_text") in (None, "")


def test_czech_fail_loud_still_raises():
    from engines.tts_lang_lock import guard_uk_tts_text
    import pytest

    with pytest.raises(RuntimeError, match="PIPELINE_LANG_MIX"):
        guard_uk_tts_text(
            "Vítejme u další epizody.",
            source_text="",
            allow_remt=False,
            fail_loud=True,
            segment_index=0,
        )


def test_segments_data_entries_keeps_original():
    from api.auto_dub_api import _segments_data_entries

    info = {
        "segments_data": [
            {
                "index": 0,
                "original": ZIP_IDX4_EN,
                "text": ZIP_IDX4_RU,
            }
        ]
    }
    out = _segments_data_entries([ZIP_IDX4_RU], info)
    assert out[0].get("original") == ZIP_IDX4_EN


def test_reissue_copies_original_one_to_one():
    from engines.pipeline_integrity.identity_guard import archive_and_reissue_ids

    old = [
        {
            "segment_id": "old-a",
            "text": ZIP_IDX4_RU,
            "original": ZIP_IDX4_EN,
            "start_ms": 0,
            "end_ms": 1000,
        }
    ]
    archived, fresh, _map = archive_and_reissue_ids(
        old,
        [ZIP_IDX4_RU],
        [{"start": 0, "end": 1000}],
    )
    assert archived
    assert fresh
    assert fresh[0].get("original") == ZIP_IDX4_EN


def test_pre_mux_allows_russian_skip_tts():
    from engines.tts_lang_lock import pre_mux_tts_integrity

    out = pre_mux_tts_integrity(
        [
            {
                "skip_tts": True,
                "tts_skip_reason": "russian_in_uk",
                "assigned_voice": "uk-UA-OstapNeural",
                "final_tts_text": "",
            },
            {
                "assigned_voice": "uk-UA-OstapNeural",
                "final_tts_text": UK_CLEAN,
                "tts_duration": 1.0,
            },
        ],
        target_lang="uk",
        simple_mode=True,
    )
    assert out["rejected_or_skipped"] == 0
    assert out["voiced"] >= 1


def test_guard_rewrites_idx4_empty_source():
    from engines.tts_lang_lock import guard_uk_tts_text

    out, meta = guard_uk_tts_text(
        ZIP_IDX4_RU,
        source_text="",
        allow_remt=False,
        fail_loud=True,
        segment_index=4,
    )
    assert meta.get("skipped") is not True
    assert meta.get("tts_lang_ok") is True
    assert "Эй" not in out
    assert "быстро" not in out
