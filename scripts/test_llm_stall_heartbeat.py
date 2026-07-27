"""
Regression test for the translation-stage LLM heartbeat pump.

Reproduces the reported failure (task 6ec6b3e2…): a slow-but-alive local LLM
(qwen2.5:7b on CPU) was false-killed by the pipeline watchdog as
``PIPELINE_STALLED`` because the translate stage emitted no heartbeat during a
long single LLM call.

We assert that ``_llm_inflight_heartbeat``:
  * refreshes the watchdog heartbeat (progress_detail.last_heartbeat_at) while an
    LLM call is in flight — so a slow model is NOT declared stalled;
  * stops reassuring once the call is cleared (does not mask a real hang);
  * ignores an overdue single call (age > cap) so the breaker/watchdog can act.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.auto_dub_api as ad  # noqa: E402
import engines.translation_adapt as ta  # noqa: E402
from engines.dub_task_state import AUTO_TASKS, STATE_LOCK  # noqa: E402


def _mk_task(task_id: str) -> None:
    with STATE_LOCK:
        AUTO_TASKS[task_id] = {
            "status": "running",
            "info": {"progress_detail": {"phase": "translate"}},
        }


def _hb_age(task_id: str) -> float:
    with STATE_LOCK:
        d = (AUTO_TASKS.get(task_id) or {}).get("info", {}).get("progress_detail", {})
        hb = float(d.get("last_heartbeat_at") or 0)
    return time.time() - hb if hb else float("inf")


def test_heartbeat_while_inflight():
    task_id = "hbtest_inflight"
    _mk_task(task_id)
    ta._llm_inflight = {"started_at": time.time(), "segment": 1, "attempt": 1}
    try:
        with ad._llm_inflight_heartbeat(task_id, interval=0.15):
            time.sleep(0.5)
            age = _hb_age(task_id)
        assert age < 0.4, f"watchdog heartbeat not refreshed during inflight (age={age})"
        with STATE_LOCK:
            d = AUTO_TASKS[task_id]["info"]["progress_detail"]
        assert d.get("llm_inflight") is True
        assert d.get("phase") == "translate"
    finally:
        ta._llm_inflight = None
    print("OK test_heartbeat_while_inflight")


def test_no_heartbeat_when_no_inflight():
    task_id = "hbtest_idle"
    _mk_task(task_id)
    ta._llm_inflight = None  # no active LLM call
    with ad._llm_inflight_heartbeat(task_id, interval=0.15):
        # seed a heartbeat, then confirm the pump does NOT keep refreshing it
        with STATE_LOCK:
            AUTO_TASKS[task_id]["info"]["progress_detail"]["last_heartbeat_at"] = time.time()
        time.sleep(0.6)
        age = _hb_age(task_id)
    assert age >= 0.5, f"pump masked idle with no inflight call (age={age})"
    print("OK test_no_heartbeat_when_no_inflight")


def test_overdue_call_not_masked():
    task_id = "hbtest_overdue"
    _mk_task(task_id)
    # A single call running far beyond any sane budget → must NOT be reassured.
    ta._llm_inflight = {"started_at": time.time() - 100000.0, "segment": 3, "attempt": 5}
    try:
        with ad._llm_inflight_heartbeat(task_id, interval=0.15):
            with STATE_LOCK:
                AUTO_TASKS[task_id]["info"]["progress_detail"]["last_heartbeat_at"] = time.time()
            time.sleep(0.6)
            age = _hb_age(task_id)
        assert age >= 0.5, f"overdue call was masked (age={age})"
    finally:
        ta._llm_inflight = None
    print("OK test_overdue_call_not_masked")


def main() -> int:
    tests = [
        test_heartbeat_while_inflight,
        test_no_heartbeat_when_no_inflight,
        test_overdue_call_not_masked,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
