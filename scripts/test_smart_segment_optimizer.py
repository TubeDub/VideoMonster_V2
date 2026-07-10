"""Tests for Smart Segment Optimizer V2."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_fits_skips_unchanged():
    from engines.smart_segment_optimizer.optimizer import optimize_segment

    text = "Короткая реплика."
    # Slot sized so estimate lands in 95–103% fill band — zero edits allowed.
    r = optimize_segment(text, slot_ms=1300, index=0, tgt_lang="ru", segment_ms=1300)
    assert r.optimized == text
    assert r.skipped is True
    assert r.skip_reason == "fits_in_slot"
    assert r.changed is False


def test_overflow_applies_level_and_stops():
    from engines.semantic_adaptation import estimate_tts_duration_ms
    from engines.smart_segment_optimizer.optimizer import optimize_segment

    text = (
        "Ну, в общем, это очень длинная реплика с вводными словами, "
        "которые можно убрать."
    )
    est = estimate_tts_duration_ms(text, "ru")
    slot = int(est * 0.88)
    r = optimize_segment(text, slot_ms=slot, index=0, tgt_lang="ru", segment_ms=slot + 40)
    assert r.changed is True
    assert r.est_ms_after <= slot
    assert r.quality.get("ok") is True


def test_quality_rollback_on_number_loss():
    from engines.smart_segment_optimizer.quality import validate_optimization

    orig = "В 2024 году компания Fiat продала 100 машин."
    bad = "Компания продала машины."
    q = validate_optimization(orig, bad, tgt_lang="ru")
    assert q.ok is False


def test_underfill_only_skips():
    from engines.smart_segment_optimizer.optimizer import optimize_segment

    text = "Коротко."
    # Long segment — underfill but no overflow; must stay unchanged.
    r = optimize_segment(text, slot_ms=8000, index=0, tgt_lang="ru", segment_ms=8000)
    assert r.optimized == text
    assert r.skip_reason == "underfill_only"
    assert r.changed is False
    assert r.underfill is True


def test_batch_optimize():
    os.environ["VM_SMART_SEGMENT_OPTIMIZER"] = "1"
    from engines.smart_segment_optimizer import is_enabled, optimize_segments

    assert is_enabled()
    segs = ["Hello world.", "Short."]
    timing = [{"start": 0, "end": 5000}, {"start": 5000, "end": 6000}]
    out, reports, meta = optimize_segments(segs, timing, tgt_lang="en", task_id="test")
    assert len(out) == 2
    assert meta["segments"] == 2


if __name__ == "__main__":
    test_fits_skips_unchanged()
    test_underfill_only_skips()
    test_overflow_applies_level_and_stops()
    test_quality_rollback_on_number_loss()
    test_batch_optimize()
    print("OK: test_smart_segment_optimizer")
