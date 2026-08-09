# -*- coding: utf-8 -*-
"""Stage19i speech-expanded split must respect video hard_end."""

from __future__ import annotations


def test_allocate_times_speech_expanded_respects_hard_end():
    from engines.closed_loop_timing import _allocate_times_speech_expanded

    chunks = [
        "Перша частина розповіді про Джорджа.",
        "Друга частина з деталями зустрічі.",
        "Фінал про Зоряні війни і Лукаса.",
    ]
    start = 174000
    hard = 178773
    allocated = _allocate_times_speech_expanded(
        chunks, start, lang="uk", hard_end_ms=hard
    )
    assert len(allocated) == 3
    assert allocated[0][1] == start
    assert allocated[-1][2] <= hard
    # Without hard_end this typically overshoots video.
    free = _allocate_times_speech_expanded(chunks, start, lang="uk")
    assert free[-1][2] >= allocated[-1][2]
