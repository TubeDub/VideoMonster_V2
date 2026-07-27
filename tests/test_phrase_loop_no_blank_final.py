# -*- coding: utf-8 -*-
"""Phrase-loop heal must not leave Translation Review Final/TTS blank."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.mt.cross_script_guard import meaning_collapse
from engines.tps.fast_qa import run_fast_qa
from engines.translation_review import build_translation_review


UK_LUCAS = (
    "Джордж-молодший сьогодні більш відомий як Джордж Лукас, а його франшиза "
    "фільму «Зоряні війни»."
)
UK_LUCAS_LOOP = (
    UK_LUCAS.rstrip(".")
    + ". більш відомий сьогодні як Джордж Лукас, а його франшиза «Зоряні війни»."
)
EN_LUCAS_LOOP = (
    "George Jr. is better known today as George Lucas and his film franchise "
    "was Star Wars. better known today as George Lucas and his film franchise "
    "was Star Wars."
)


def test_fast_qa_does_not_fail_healable_lucas_loop_as_phrase_loop():
    r = run_fast_qa(
        EN_LUCAS_LOOP,
        UK_LUCAS_LOOP,
        context={"source_lang": "en", "target_lang": "uk"},
    )
    assert "phrase_loop" not in r.reason_codes
    # Deflated clean text must PASS.
    r2 = run_fast_qa(
        EN_LUCAS_LOOP,
        UK_LUCAS,
        context={"source_lang": "en", "target_lang": "uk"},
    )
    assert r2.passed, r2.reason_codes


def test_meaning_collapse_healable_phrase_loop_alone_is_not_collapse():
    # Healable echo must not alone force meaning_collapse (no TTS wipe).
    assert meaning_collapse(EN_LUCAS_LOOP, UK_LUCAS_LOOP, target_lang="uk") is None
    assert meaning_collapse(EN_LUCAS_LOOP, UK_LUCAS, target_lang="uk") is None


def test_review_shows_final_after_phrase_loop_tts_wipe():
    info = {
        "source_segments": [EN_LUCAS_LOOP],
        "source_lang": "en",
        "target_lang": "uk",
        "segments_data": [
            {
                "index": 0,
                "tts_blocked": True,
                "skip_tts": True,
                "tts_blocked_reason": "meaning_collapse",
                "tps_reason_codes": ["phrase_loop", "meaning_collapse"],
                "tqe_status": "FAIL_MANUAL_REVIEW",
                "needs_manual_review": True,
                "final_text": "",
                "text": "",
                "approved_text": "",
                "naturalized_text": UK_LUCAS,
                "original_text": EN_LUCAS_LOOP,
            }
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": UK_LUCAS,
                "naturalized_text": UK_LUCAS,
                "final_text": "",
                "whisper_text": EN_LUCAS_LOOP,
                "reason_codes": ["phrase_loop", "meaning_collapse"],
                "tqe_status": "FAIL_MANUAL_REVIEW",
            }
        ],
    }
    review = build_translation_review(info)
    rows = review.get("segments") or review.get("rows") or []
    assert rows, list(review.keys())
    row = rows[0]
    assert "Джордж Лукас" in row["final_text"]
    assert "Зоряні війни" in row["text_for_tts"]
    assert row["final_text"].strip()
    assert row["text_for_tts"].strip()
