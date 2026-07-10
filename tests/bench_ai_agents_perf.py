"""Before/after benchmark for AI Core 3.0 (multi-agent coordinator).

Not collected by pytest (filename does not start with ``test_``). Run with:

    set PYTHONPATH=. && set VM_LLM_AUTODISCOVER=0 && python tests/bench_ai_agents_perf.py

Compares, for the SAME batch of segments and the SAME simulated slow LLM, the
legacy single-engine timing-aware path against the new multi-agent coordinator:

  BEFORE : engines.timing_aware_translation.adapt_segments_to_timing (legacy)
  AFTER  : engines.ai_core.agents.AgentCoordinator.run (cheap-first, skips
           unneeded agents, per-agent cache)
  AFTER* : the coordinator run a SECOND time (warm per-agent cache)

The multi-agent path fires the LLM only for segments the cheap rule path cannot
fit, skips Timing/Grammar entirely for segments that already fit, and reuses
per-agent caches, so it does strictly less work than forcing one big engine on
every segment.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest.mock as mock

os.environ.setdefault("VM_LLM_AUTODISCOVER", "0")
os.environ["VM_LLM_CACHE_DIR"] = tempfile.mkdtemp(prefix="vm_bench_cache_")

import engines.translation_adapt as ta  # noqa: E402
from engines.ai_core.agents import AgentCoordinator  # noqa: E402
from engines.timing_aware_translation import adapt_segments_to_timing  # noqa: E402

LLM_LATENCY_S = 0.05
_CALLS = {"n": 0}

UK_SHORT = "Це коротке речення."
UK_LONG = (
    "Це дуже довге українське речення для перевірки адаптації, "
    "яке напевно не поміститься у відведений короткий слот озвучування зовсім."
)


def _fake_chat(prompt, **kwargs):
    _CALLS["n"] += 1
    time.sleep(LLM_LATENCY_S)
    return "Стисле україномовне речення."


def _batch(n: int):
    segs, timing, srcs = [], [], []
    for i in range(n):
        overflow = (i % 2 == 0)
        segs.append(UK_LONG if overflow else UK_SHORT)
        timing.append((0, 1300) if overflow else (0, 8000))
        srcs.append("A long English source line." if overflow else "Short.")
    return segs, timing, srcs


def _strategy():
    return {"use_llm": True, "speed_mode": "balanced", "llm_policy": "problem_only",
            "model": "mock"}


def _run_legacy(segs, timing, srcs):
    _CALLS["n"] = 0
    ta.reset_llm_budget()
    t0 = time.monotonic()
    with mock.patch.object(ta, "llm_rephrase_available", lambda: True), \
         mock.patch.object(ta, "_llm_chat", _fake_chat):
        adapt_segments_to_timing(segs, timing, srcs, src_lang="en", tgt_lang="uk",
                                 speed_mode="balanced")
    return time.monotonic() - t0, _CALLS["n"]


def _run_agents(segs, timing, srcs, *, task_id="bench"):
    _CALLS["n"] = 0
    ta.reset_llm_budget()
    coord = AgentCoordinator(task_id, {"content_type": "movie"}, _strategy())
    t0 = time.monotonic()
    with mock.patch.object(ta, "llm_rephrase_available", lambda: True), \
         mock.patch.object(ta, "_llm_chat", _fake_chat):
        coord.run(segs, timing, srcs, src_lang="en", tgt_lang="uk", raw_mt_segments=segs)
    return time.monotonic() - t0, _CALLS["n"]


def main():
    n = 24
    segs, timing, srcs = _batch(n)
    print(f"Simulated LLM latency: {LLM_LATENCY_S*1000:.0f} ms/call, {n} segments "
          f"({n//2} overflow, {n//2} fit)\n")

    # Force single-thread so the wall-time comparison reflects work done, not
    # thread count (both paths share the same worker sizing otherwise).
    with mock.patch.dict(os.environ, {"VM_ADAPT_MAX_WORKERS": "1"}):
        b_s, b_c = _run_legacy(segs, timing, srcs)
        a_s, a_c = _run_agents(segs, timing, srcs, task_id="cold")
        w_s, w_c = _run_agents(segs, timing, srcs, task_id="cold")  # warm cache

    rows = [
        ("BEFORE (legacy single-engine)", b_s, b_c),
        ("AFTER  (multi-agent, cold)", a_s, a_c),
        ("AFTER* (multi-agent, warm cache)", w_s, w_c),
    ]
    print(f"{'regime':<36}{'wall_s':>10}{'llm_calls':>12}")
    for name, s, c in rows:
        print(f"{name:<36}{s:>10.2f}{c:>12}")

    print(f"\nLLM calls: BEFORE={b_c}  AFTER={a_c}  "
          f"({'-' if b_c>=a_c else '+'}{abs(100*(b_c-a_c)/max(b_c,1)):.0f}%)")
    print(f"Wall time: BEFORE={b_s:.2f}s  AFTER={a_s:.2f}s  warm={w_s:.2f}s  "
          f"speedup(warm)={b_s/max(w_s,1e-6):.1f}x")


if __name__ == "__main__":
    main()
