"""Regression tests — repetition guard (TZ §8/§11).

Verifies repeated sentences and repeated word-runs are removed before TTS,
while unique content and meaning are preserved (no over-deletion).
"""

from __future__ import annotations

from engines.repetition_guard import (
    dedupe_segment_texts,
    has_repetition,
    remove_repeated_sentences,
)


def test_exact_sentence_repeat_removed():
    text = "Джордж їхав додому. Джордж їхав додому."
    out, changed = remove_repeated_sentences(text)
    assert changed is True
    assert out == "Джордж їхав додому."


def test_stress_marked_repeat_removed():
    # TTS stress diacritics must not hide a duplicate.
    text = "Але́ коли́ він їхав, він боя́вся. Але коли він їхав, він боявся."
    out, changed = remove_repeated_sentences(text)
    assert changed is True
    assert out.count("боя") == 1 or out.count("боявся") == 1


def test_repeated_word_run_collapsed():
    text = "він робив поворот він робив поворот а потім стало темно"
    out, changed = remove_repeated_sentences(text)
    assert changed is True
    assert out == "він робив поворот а потім стало темно"


def test_truncated_repeat_keeps_longer():
    text = "Вісімнадцятирічний Джордж їхав додому через місто. Вісімнадцятирічний Джордж їхав додому."
    out, changed = remove_repeated_sentences(text)
    assert changed is True
    # The fuller sentence is kept.
    assert "через місто" in out
    assert out.count("Джордж їхав додому") == 1


def test_unique_sentences_preserved():
    text = "Перше унікальне речення. Друге зовсім інше речення."
    out, changed = remove_repeated_sentences(text)
    assert changed is False
    assert out == text


def test_diverging_sentences_not_removed():
    # Same prefix but different meaning → must be KEPT (no meaning loss, TZ §4).
    text = (
        "Коли Джордж робив поворот, щось сталося. "
        "Коли Джордж робив поворот, а потім стало темно зовсім."
    )
    _out, changed = remove_repeated_sentences(text)
    assert changed is False


def test_empty_and_single():
    assert remove_repeated_sentences("") == ("", False)
    assert remove_repeated_sentences("Одне речення.") == ("Одне речення.", False)


def test_dedupe_segment_texts_reports_indices():
    texts = ["A. A.", "Унікальне.", "x y z x y z"]
    cleaned, changed_idx = dedupe_segment_texts(texts)
    assert changed_idx == [0, 2]
    assert cleaned[1] == "Унікальне."


def test_has_repetition_flag():
    assert has_repetition("Те саме. Те саме.") is True
    assert has_repetition("Унікальний текст без повторів.") is False
