"""DSAL max-pack: stall threshold, aggressive compress, flags, emergency atempo."""

from __future__ import annotations


def test_tts_stall_threshold_raised():
    from engines.pipeline_watchdog import STALL_IDLE_SEC

    assert STALL_IDLE_SEC["tts"] >= 240.0


def test_rule_compress_keeps_partial_and_shortens():
    from engines.dsal.core import _rule_compress_uk, analyze_duration

    # Verbose UK that overflows a tight slot — must get shorter, not all-or-nothing.
    text = (
        "У той момент, коли Джордж надзвичайно швидко під'їхав до перехрестя "
        "прямо біля дому, він насправді почав повертати, і потім все потемніло "
        "раптово і несподівано без жодних попереджень."
    )
    slot = 2800
    before = analyze_duration(slot_ms=slot, text=text, tgt_lang="uk")
    assert before.compress_required
    out, stages = _rule_compress_uk(
        text, slot_ms=slot, source_hint="when George turned everything went black", tgt_lang="uk"
    )
    assert out
    assert len(out) < len(text)
    assert stages
    after = analyze_duration(slot_ms=slot, text=out, tgt_lang="uk")
    assert after.predicted_tts_ms <= before.predicted_tts_ms


def test_adapt_compress_overflow_seg7_style():
    from engines.dsal import adapt_duration_semantic

    # Live #7-ish: slot 5600 with long UK → must compress or at least change.
    text = (
        "У той момент, коли він почув цей надзвичайно гучний звук прямо біля дому, "
        "і потім все насправді потемніло раптово і несподівано, "
        "і життя змінилося назавжди без жодних попереджень."
    )
    res = adapt_duration_semantic(
        text,
        source_hint="when he hears this really loud screeching sound then everything went black",
        slot_ms=5600,
        tgt_lang="uk",
        actual_tts_ms=6932,
    )
    assert res.adaptation_executed is True
    assert len(res.text) < len(text)


def test_block_merge_pulls_green_with_spare():
    from engines.dsal.block_merge import detect_block_candidates

    segs = [
        {
            "text": "довгий текст який явно не вміщується в короткий слот озвучки дубляжу",
            "slot_ms": 2000,
            "dsal_band": "red",
            "tts_ms": 4500,
        },
        {
            "text": "коротко",
            "slot_ms": 5000,
            "dsal_band": "green",
            "tts_ms": 800,
        },
    ]
    plans = detect_block_candidates(segs, tgt_lang="uk")
    assert plans
    assert plans[0].indices == [0, 1]


def test_openddf_summary_reads_post_tts_qa_adaptation():
    from engines.segment_timing_qa import build_openddf_full_report

    report = build_openddf_full_report(
        {
            "task_id": "t1",
            "target_lang": "uk",
            "segments_data": [
                {"index": 0, "start_time_ms": 0, "final_tts_duration_ms": 1000},
            ],
            "post_tts_qa": {"adaptation_executed": True, "rewritten": 3},
            "dsal_pre_lock": {"adapted": 5},
        }
    )
    assert report["summary"]["adaptation_status"] == "ADAPTATION EXECUTED"
    assert "ADAPTATION EXECUTED" in report["flags"]


def test_emergency_atempo_cap_helpers():
    from engines.timing_fit import (
        _ATEMPO_ABSOLUTE_MAX,
        _ATEMPO_EMERGENCY_MAX,
        _atempo_hard_cap,
        _gentle_atempo_factor,
    )

    assert _atempo_hard_cap(1.05) == _ATEMPO_ABSOLUTE_MAX
    assert _atempo_hard_cap(1.12) == _ATEMPO_EMERGENCY_MAX
    # Severe need with emergency request → can exceed 1.05
    factor = _gentle_atempo_factor(1.20, max_atempo=_ATEMPO_EMERGENCY_MAX)
    assert factor > 1.05
    assert factor <= _ATEMPO_EMERGENCY_MAX
