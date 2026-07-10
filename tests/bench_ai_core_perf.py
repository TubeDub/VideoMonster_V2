"""Manual before/after benchmark for the AI Core hang fix (P0).

Not collected by pytest (filename does not start with ``test_``). Run with:

    set PYTHONPATH=. && python tests/bench_ai_core_perf.py   (Windows)
    PYTHONPATH=. python tests/bench_ai_core_perf.py           (POSIX)

Simulates a SLOW local LLM (fixed latency per call) and measures, for the same
batch of segments, the number of LLM calls + wall time under three regimes at
the ``adapt_segment_ai`` level (deterministic — no real endpoint, no threads):

  BEFORE : llm_policy="always"       (old AI Core default = max_quality: full
           LLM rewrite forced on EVERY overflow segment, 10 variants)
  AFTER  : llm_policy="problem_only" (balanced decision engine: LLM only for
           segments the rule prep cannot fit)
  FAST   : llm_policy="off"          (rule-based only, never LLM)

Same rule prep + scoring logic in all regimes → the difference is purely the
Decision Engine avoiding unnecessary LLM work.
"""

from __future__ import annotations

import time
import unittest.mock as mock

import engines.ai_adaptation_engine as eng
import engines.translation_adapt as ta

LLM_LATENCY_S = 0.05
_CALLS = {"n": 0}


def _fake_chat(prompt, **kwargs):
    _CALLS["n"] += 1
    time.sleep(LLM_LATENCY_S)
    return "Стисле україномовне речення для озвучення."


def _segments(n: int):
    short = "Це коротке речення."
    long = (
        "Це дуже довге українське речення для перевірки адаптації, "
        "яке напевно не поміститься у відведений короткий слот озвучування."
    )
    rows = []
    for i in range(n):
        is_long = (i % 2 == 0)
        rows.append((
            long if is_long else short,
            "A long English source line." if is_long else "Short line.",
            1500 if is_long else 6000,
        ))
    return rows


def _run(regime: str, policy: str):
    _CALLS["n"] = 0
    ta.reset_llm_budget()
    ta.begin_llm_capture()
    overrides = {"llm_policy": policy}
    if policy == "always":
        overrides.update({"min_variants": 10, "min_rounds": 4, "max_rounds": 6})
    else:
        overrides.update({"min_variants": 5, "min_rounds": 2, "max_rounds": 4})
    eng.set_adaptation_profile_override(overrides)

    rows = _segments(24)
    t0 = time.monotonic()
    with mock.patch.object(ta, "llm_rephrase_available", lambda: True), \
         mock.patch.object(ta, "_llm_chat", _fake_chat):
        for i, (text, src, slot) in enumerate(rows):
            eng.adapt_segment_ai(text, source_hint=src, slot_ms=slot, tgt_lang="uk", index=i)
    elapsed = time.monotonic() - t0
    eng.set_adaptation_profile_override(None)
    return elapsed, _CALLS["n"], len(rows)


def main():
    print(f"Simulated LLM latency: {LLM_LATENCY_S*1000:.0f} ms/call, 24 segments "
          f"(12 overflow, 12 fit)\n")
    rows = [
        ("BEFORE (always/max_quality)", *_run("before", "always")),
        ("AFTER  (balanced)", *_run("after", "problem_only")),
        ("FAST   (rule only)", *_run("fast", "off")),
    ]
    print(f"{'regime':<30}{'wall_s':>10}{'llm_calls':>12}{'segments':>10}")
    for name, elapsed, calls, n in rows:
        print(f"{name:<30}{elapsed:>10.2f}{calls:>12}{n:>10}")

    b_s, b_c = rows[0][1], rows[0][2]
    a_s, a_c = rows[1][1], rows[1][2]
    print(f"\nLLM calls: BEFORE={b_c}  AFTER={a_c}  "
          f"(-{100*(b_c-a_c)/max(b_c,1):.0f}%)")
    print(f"Wall time: BEFORE={b_s:.2f}s  AFTER={a_s:.2f}s  "
          f"speedup={b_s/max(a_s,1e-6):.1f}x")


if __name__ == "__main__":
    main()
