# -*- coding: utf-8 -*-
"""Restore sentence endings after shorten — no silent mid-thought cuts."""

from __future__ import annotations

from engines.semantic_meaning import (
    is_truncated_adaptation,
    restore_terminal_close,
)


def test_restore_terminal_close_when_en_complete():
    uk = "Тож Джордж-молодший вирішив, що він не хоче займатися автогонками"
    en = "So George Jr. had decided that he really didn't want to race cars anymore."
    out = restore_terminal_close(uk, original=en)
    assert out.endswith(".")


def test_no_force_dot_on_whisper_cut():
    uk = "І в той момент його батько купив йому італійський Фіат"
    en = "And at that point his father actually bought him a small Italian car called the Fiat,"
    out = restore_terminal_close(uk, original=en)
    assert not out.endswith(".")


def test_paraphrase_with_period_not_truncated_tail():
    long = (
        "Через два тижні Джордж-молодший лежав на лікарняному ліжку "
        "у відділенні інтенсивної терапії в місцевій лікарні."
    )
    short = "Через два тижні Джордж-молодший лежав у реанімації місцевої лікарні."
    assert is_truncated_adaptation(long, short) is False


def test_missing_period_on_shorten_is_truncation_signal():
    long = "Його фільм стане частиною найбільш успішної кінофраншизи всіх часів."
    short = "Його фільм стане частиною франшизи"
    assert is_truncated_adaptation(long, short) is True
