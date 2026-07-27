# -*- coding: utf-8 -*-
"""Film/full_dub styles mute original; CJK STT merge keeps multi-segment coverage."""

from __future__ import annotations


def test_cinematic_full_dub_mute():
    from engines.dub_style_presets import resolve_dub_style

    r = resolve_dub_style("cinematic")
    assert r["mix_mode"] == "full_dub"
    assert r["mix_volumes"]["original_volume"] == 0.0

    # Stale UI 20% must not undo cinematic mute
    r20 = resolve_dub_style("cinematic", original_volume=0.2)
    assert r20["mix_mode"] == "full_dub"
    assert r20["mix_volumes"]["original_volume"] == 0.0

    # Explicit higher underlay (user choice) still allowed
    r40 = resolve_dub_style("cinematic", original_volume=0.4)
    assert r40["mix_mode"] == "custom"
    assert abs(r40["mix_volumes"]["original_volume"] - 0.4) < 1e-6


def test_modern_professional_mute():
    from engines.dub_style_presets import resolve_dub_style

    for sid in ("modern", "professional"):
        r = resolve_dub_style(sid, original_volume=0.2)
        assert r["mix_mode"] == "full_dub", sid
        assert r["mix_volumes"]["original_volume"] == 0.0, sid


def test_documentary_keeps_underlay():
    from engines.dub_style_presets import resolve_dub_style

    r = resolve_dub_style("documentary")
    assert r["mix_mode"] == "custom"
    assert r["mix_volumes"]["original_volume"] > 0.1


def test_cjk_merge_keeps_multiple_blocks():
    from engines.segment_merger import merge_stt_by_sentences, merge_stt_segments

    segs = [
        "我们陆家八代单成",
        "此思单宝",
        "如今啊",
        "你怀孕了",
        "陆家也厚了",
        "要是能一几德南",
        "那就更完美了",
        "妈",
        "教师我一身的",
        "无论事儿是你",
        "我都喜欢",
        "这话还够过",
        "我怀孕了",
        "你跟面前没成余",
    ]
    # Timings matching real Whisper probe (gaps + a long pause later)
    timing = [
        {"start": 1350, "end": 3130},
        {"start": 3130, "end": 4070},
        {"start": 4450, "end": 5390},
        {"start": 5390, "end": 6570},
        {"start": 6570, "end": 7510},
        {"start": 7930, "end": 9790},
        {"start": 9790, "end": 12410},
        {"start": 13850, "end": 14230},
        {"start": 14730, "end": 16110},
        {"start": 16110, "end": 17870},
        {"start": 17870, "end": 18690},
        {"start": 34340, "end": 35800},
        {"start": 35800, "end": 37100},
        {"start": 38380, "end": 40180},
    ]
    m1, t1 = merge_stt_by_sentences(segs, timing)
    m2, t2 = merge_stt_segments(segs, timing)
    assert len(m1) >= 3, m1
    assert len(m2) >= 3, m2
    assert t1[-1]["end"] >= 37000
    assert t2[-1]["end"] >= 37000


def test_sparse_whisper_cache_rejected(tmp_path, monkeypatch):
    from engines import pipeline_cache as pc

    monkeypatch.setattr(pc, "_cache_root", lambda _app: tmp_path)
    app = tmp_path
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 100)
    pc.save_whisper_cache(
        app,
        str(video),
        model_size="tiny",
        source_lang=None,
        source_text="你怀孕了",
        timing_map=[{"start": 1200, "end": 5570}],
        detected_lang="zh",
    )
    hit = pc.load_whisper_cache(app, str(video), model_size="tiny", source_lang=None)
    assert hit is None
