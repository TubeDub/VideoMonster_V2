"""Sentence & word integrity gate (AutoDub audit TЗ §3/§4/§6/§8).

Guarantees: no empty / NULL / space-only / mid-word / unfinished text reaches
TTS, and broken candidates are reverted to the fullest COMPLETE alternative
(never clipped, never empty).
"""

from __future__ import annotations

from engines.sentence_integrity import (
    enforce_pre_tts_integrity,
    enforce_tts_integrity,
    normalize_spaces,
    validate_tts_text,
)


# ---- validate_tts_text -----------------------------------------------------

def test_valid_complete_sentence():
    ok, issues = validate_tts_text("Джордж поїхав додому на вечерю.")
    assert ok is True
    assert issues == []


def test_empty_and_whitespace_rejected():
    assert validate_tts_text("")[0] is False
    assert validate_tts_text("   ")[0] is False
    assert validate_tts_text("\t \n")[0] is False


def test_null_and_empty_json_rejected():
    for bad in ("null", "None", "NULL", "{}", "[]", "()", "\"\""):
        ok, issues = validate_tts_text(bad)
        assert ok is False, bad


def test_punctuation_only_rejected():
    ok, _ = validate_tts_text("...")
    assert ok is False
    ok2, _ = validate_tts_text("—")
    assert ok2 is False


def test_mid_word_hyphen_rejected():
    ok, issues = validate_tts_text("Джордж-молодший подався до Каліфор-")
    assert ok is False
    assert "mid_word" in issues


def test_dangling_connector_rejected():
    ok, issues = validate_tts_text("Він поїхав додому і")
    assert ok is False
    assert "dangling_connector" in issues


def test_incomplete_sentence_no_terminal_rejected():
    ok, issues = validate_tts_text(
        "Джордж молодший подав заяву на престижну кінотехніку в університеті"
    )
    assert ok is False
    assert "incomplete_sentence" in issues


def test_short_phrase_without_terminal_is_ok():
    # A short caption-like phrase must NOT be flagged as incomplete.
    ok, _ = validate_tts_text("Привіт світ")
    assert ok is True


def test_question_and_exclamation_ok():
    assert validate_tts_text("Це справді так?")[0] is True
    assert validate_tts_text("Яка чудова ідея!")[0] is True


def test_closing_quote_after_terminal_ok():
    assert validate_tts_text("Він сказав: «Я знаю людей».")[0] is True


# ---- normalize_spaces ------------------------------------------------------

def test_normalize_collapses_space_runs():
    assert normalize_spaces("Слово   з    пробілами.") == "Слово з пробілами."


# ---- enforce_tts_integrity -------------------------------------------------

def test_valid_candidate_kept():
    d = enforce_tts_integrity("Повне речення тут.", fallbacks=["Інше повне речення."])
    assert d["chosen"] == "candidate"
    assert d["changed"] is False
    assert d["text"] == "Повне речення тут."


def test_broken_candidate_reverts_to_full_fallback():
    d = enforce_tts_integrity(
        "Джордж подався до Каліфор-",
        fallbacks=["Джордж подався до Каліфорнії, щоб вчитися."],
    )
    assert d["chosen"] == "fallback[0]"
    assert d["changed"] is True
    assert d["text"] == "Джордж подався до Каліфорнії, щоб вчитися."
    assert "mid_word" in d["issues"]


def test_empty_candidate_reverts_never_empty():
    d = enforce_tts_integrity(
        "",
        fallbacks=["", "Повний запасний варіант."],
    )
    assert d["text"] == "Повний запасний варіант."
    assert d["chosen"] == "fallback[1]"


def test_skips_broken_fallback_picks_next_complete():
    d = enforce_tts_integrity(
        "обрізаний-",
        fallbacks=["теж обрізаний-", "А ось повне речення нарешті."],
    )
    assert d["text"] == "А ось повне речення нарешті."
    assert any(r["issues"] for r in d["rejected"])


def test_source_last_resort():
    d = enforce_tts_integrity("", fallbacks=["", "{}"], source="Source line here.")
    assert d["chosen"] == "source"
    assert d["text"] == "Source line here."


def test_english_source_not_used_for_ukrainian_dub():
    d = enforce_tts_integrity(
        "Обрізане речення без кінця",
        fallbacks=[
            "Теж обрізане без",
            "Повне українське речення тут.",
        ],
        source="And at that point his father actually bought him a small Italian car.",
        tgt_lang="uk",
    )
    assert d["text"] == "Повне українське речення тут."
    assert d["chosen"] == "fallback[1]"
    assert d["chosen"] != "source"


def test_never_returns_empty_even_with_no_alternatives():
    d = enforce_tts_integrity("слово-", fallbacks=[], source="")
    assert d["text"]  # non-empty
    assert d["chosen"] == "candidate_forced"


# ---- enforce_pre_tts_integrity (batch, audit-driven) -----------------------

def test_batch_reverts_broken_segments_using_audits():
    segments = [
        "Нормальне речення.",
        "Обрізане слово-",  # broken → revert
        "",  # empty → revert
    ]
    audits = [
        {"index": 0, "naturalized_text": "Нормальне речення."},
        {"index": 1, "naturalized_text": "Обрізане слово тут повністю написане зараз."},
        {"index": 2, "naturalized_text": "Другий повний варіант речення тут."},
    ]
    fixed, report = enforce_pre_tts_integrity(segments, audits=audits)
    assert fixed[0] == "Нормальне речення."
    assert fixed[1] == "Обрізане слово тут повністю написане зараз."
    assert fixed[2] == "Другий повний варіант речення тут."
    assert report["fixed"] == 2
    assert report["fixed_indices"] == [1, 2]


def test_batch_no_change_when_all_valid():
    segments = ["Перше речення.", "Друге речення!"]
    fixed, report = enforce_pre_tts_integrity(segments, audits=[])
    assert fixed == segments
    assert report["fixed"] == 0
