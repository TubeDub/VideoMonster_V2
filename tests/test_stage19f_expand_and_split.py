# -*- coding: utf-8 -*-
"""Stage 19f: expand must execute + aggressive post-restore split."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_long_uk_paragraph() -> str:
    sentences = [
        "Джордж молодший завжди любив кіно і мріяв потрапити на знімальний майданчик.",
        "Він зустрів Хаскелла Векслера і розповів про свою камеру та старий Фіат.",
        "Ще з дитинства він дивився Зоряні війни і вірив у силу фантазії.",
        "Потім він вирішив змінити життя і подати документи на роботу в студії.",
        "Незважаючи на сумніви батьків, він не здавався і працював щодня.",
        "Зрештою його помітили, і він отримав шанс показати свій потенціал.",
        "Він згадав аварію на треку, але все одно продовжив рухатися вперед.",
        "Камера, роботи і гоночні мрії лишилися з ним назавжди.",
    ]
    return " ".join(sentences * 4)


def test_force_split_until_fit_80s_into_4s_at_least_3():
    from engines.text_slot_fit import (
        estimate_tts_ms,
        force_split_until_fit,
        should_force_split,
    )

    text = _make_long_uk_paragraph()
    for slot_ms in (4000, 20000):
        assert should_force_split(text, slot_ms, "uk") is True
        chunks = force_split_until_fit(text, slot_ms, "uk", max_children=8)
        assert len(chunks) >= 3, (slot_ms, len(chunks))
        for c in chunks:
            pred = estimate_tts_ms(c, "uk")
            # Child packs aimed at slot; predicted must not exceed slot×1.25.
            assert pred <= int(slot_ms * 1.25) + 1 or len(chunks) >= 8, (
                slot_ms,
                pred,
                len(c.split()),
                c[:60],
            )


def test_restore_split_children_each_fill_le_125():
    from engines.closed_loop_timing import try_stage19e_post_restore_split
    from engines.text_slot_fit import estimate_tts_ms

    text = _make_long_uk_paragraph()
    slot_ms = 20000
    seg = {
        "plain_text": text,
        "text": text,
        "final_text": text,
        "final_tts_text": text,
        "tts_ms": 80000,
        "playback_duration": 80000,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "segment_id": "p19f",
        "needs_post_restore_split": True,
        "truncation_blocked": True,
    }
    segments = [seg]
    timing_map = [{"start": 0, "end": slot_ms}]
    ok = try_stage19e_post_restore_split(
        segments_data=segments,
        source_segments=["He loved cinema and met Wexler about the camera."],
        timing_map=timing_map,
        audits=None,
        idx=0,
        lang="uk",
    )
    assert ok is True
    assert len(segments) >= 3
    assert all(s.get("post_restore_split") for s in segments)
    for s in segments:
        child_slot = max(1, int(s.get("slot_ms") or 1))
        pred = estimate_tts_ms(str(s.get("plain_text") or ""), "uk")
        fill = pred / float(child_slot)
        assert fill <= 1.25 + 1e-6, (fill, child_slot, pred)
        assert float((s.get("stage19f") or {}).get("fill_ratio") or fill) <= 1.25 + 1e-6


def test_underflow_expand_changes_text_and_retts(tmp_path: Path):
    from engines.closed_loop_timing import apply_stage19b_rule_text_fit

    slot_ms = 8000
    tts_ms = slot_ms - 2000
    short = "Він пішов."
    raw = (
        "Він довго йшов тією дорогою і думав про те, як саме тоді "
        "вирішив змінити своє життя назавжди і зустрів Векслера."
    )
    seg = {
        "plain_text": short,
        "text": short,
        "final_tts_text": short,
        "raw_translation": raw,
        "semantic_engine_text": raw,
        "file": str(tmp_path / "u.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "first_tts_duration_ms": tts_ms,
    }
    (tmp_path / "u.wav").write_bytes(b"RIFF")
    timing_map = [{"start": 0, "end": slot_ms}]
    regen_calls: list[str] = []

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        delta = measured - slot_ms
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot_ms,
            slot_duration=slot_ms,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="underflow" if delta < -350 else "ok",
        )

    def regen(text, **kwargs):
        regen_calls.append(text)
        out = tmp_path / f"regen_{len(regen_calls)}.wav"
        out.write_bytes(b"RIFF")
        return str(out), min(slot_ms, max(tts_ms + 900, len(text) * 40))

    with patch(
        "engines.closed_loop_timing.build_timing_budget", side_effect=_fake_budget
    ), patch(
        "engines.closed_loop_timing.measure_actual_ms",
        side_effect=lambda s, **k: int(s.get("playback_duration") or s.get("tts_ms") or 0),
    ), patch(
        "engines.closed_loop_timing.apply_dynamic_pause_engine",
        return_value={"applied": False},
    ), patch(
        "engines.closed_loop_timing._apply_light_atempo_after_fit",
        side_effect=lambda seg, budget, **k: budget,
    ):
        budget = _fake_budget(seg, 0, timing_map)
        budget, attempted = apply_stage19b_rule_text_fit(
            seg,
            0,
            timing_map,
            budget,
            source_hint="He walked a long road and met Wexler.",
            target_lang="uk",
            voice="uk-UA-OstapNeural",
            work_dir=tmp_path,
            regen_fn=regen,
        )

    assert attempted is True
    assert bool(seg.get("expand_executed")) is True
    assert len(regen_calls) >= 1
    assert int(budget.rewrite_iterations or 0) >= 1
    assert str(seg.get("plain_text") or "") != short
    assert seg.get("stage19f") is not None
    assert seg["stage19f"].get("expand_executed") is True
    assert seg["stage19f"].get("algorithm_reason") in (
        "TextSlotFitExpand",
        "TextThenAtemo",
    )


def test_expand_no_change_dead_air_not_text_slot_fit_expand(tmp_path: Path):
    from engines.closed_loop_timing import apply_stage19b_rule_text_fit
    from engines.text_slot_fit import TextFitResult

    slot_ms = 6000
    tts_ms = slot_ms - 2200
    text = "Ок."
    seg = {
        "plain_text": text,
        "text": text,
        "final_tts_text": text,
        "raw_translation": text,
        "semantic_engine_text": text,
        "file": str(tmp_path / "d.wav"),
        "tts_ms": tts_ms,
        "playback_duration": tts_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
    }
    (tmp_path / "d.wav").write_bytes(b"RIFF")
    timing_map = [{"start": 0, "end": slot_ms}]

    def _fake_budget(seg, idx, timing_map):
        from engines.closed_loop_timing import TimingBudget

        measured = int(seg.get("playback_duration") or seg.get("tts_ms") or 0)
        delta = measured - slot_ms
        return TimingBudget(
            index=idx,
            slot_start=0,
            slot_end=slot_ms,
            slot_duration=slot_ms,
            tts_duration=measured,
            measured_duration=measured,
            delta=delta,
            overflow=max(0, delta),
            underflow=max(0, -delta),
            status="underflow",
        )

    with patch(
        "engines.closed_loop_timing.build_timing_budget", side_effect=_fake_budget
    ), patch(
        "engines.closed_loop_timing.measure_actual_ms",
        side_effect=lambda s, **k: int(s.get("playback_duration") or s.get("tts_ms") or 0),
    ), patch(
        "engines.closed_loop_timing.apply_dynamic_pause_engine",
        return_value={"applied": False},
    ), patch(
        "engines.closed_loop_timing._apply_light_atempo_after_fit",
        side_effect=lambda seg, budget, **k: budget,
    ), patch(
        "engines.text_slot_fit.expand_to_fill",
        return_value=(text, []),
    ), patch(
        "engines.text_slot_fit.fit_text_to_slot",
    ) as fit_mock:
        fit_mock.return_value = TextFitResult(
            text=text,
            slot_ms=slot_ms,
            predicted_ms_before=tts_ms,
            predicted_ms_after=tts_ms,
            changed=False,
            action="none",
            strategy="ok",
            fill_ratio=tts_ms / float(slot_ms),
            atempo=1.0,
            reasons=[],
        )
        budget = _fake_budget(seg, 0, timing_map)
        budget, attempted = apply_stage19b_rule_text_fit(
            seg,
            0,
            timing_map,
            budget,
            source_hint="Ok.",
            target_lang="uk",
            voice="uk-UA-OstapNeural",
            work_dir=tmp_path,
            regen_fn=lambda *a, **k: (str(tmp_path / "x.wav"), tts_ms),
        )

    assert attempted is True
    assert bool(seg.get("expand_executed")) is False
    assert budget.final_status == "dead_air_risk"
    algo = str(seg.get("algorithm_reason") or "")
    assert algo != "TextSlotFitExpand"
    assert (seg.get("stage19f") or {}).get("algorithm_reason") != "TextSlotFitExpand"


def test_sanitize_forbids_false_text_slot_fit_expand():
    from engines.closed_loop_timing import _stage19d_sanitize_algorithm_reason

    r = _stage19d_sanitize_algorithm_reason(
        "TextSlotFitExpand",
        expand_executed=False,
        shorten_executed=False,
        split_executed=False,
        delta_ms=-900,
        underflow_ms=900,
        overflow_ms=0,
    )
    assert r == "dead_air_risk"
