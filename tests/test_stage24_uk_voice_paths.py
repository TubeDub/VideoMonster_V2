# -*- coding: utf-8 -*-
"""Stage 24 hard locks: UK voice, Edge never gets mykyta, cache v3, abs paths."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_force_uk_allows_explicit_tetiana_not_cs():
    from engines.tts_lang_lock import force_uk_tts_identity

    t = force_uk_tts_identity(target_lang="uk", engine_id="tts_uk", voice="tetiana")
    assert t["voice"] == "tetiana"
    d = force_uk_tts_identity(target_lang="uk", engine_id="tts_uk", voice="")
    assert d["voice"] == "mykyta"
    e = force_uk_tts_identity(
        target_lang="uk", engine_id="edge-offline", voice="mykyta"
    )
    assert e["voice"].startswith("uk-UA-")
    assert e["voice"] != "mykyta"


def test_edge_voice_resolve_never_mykyta():
    from engines.tts_backends import resolve_voice_for_backend
    from engines.tts_lang_lock import force_uk_tts_identity

    v = resolve_voice_for_backend("mykyta", "edge-offline")
    assert v.startswith("uk-UA-")
    ident = force_uk_tts_identity(
        target_lang="uk", engine_id="edge-offline", voice="cs-CZ-AntoninNeural"
    )
    assert ident["voice"].startswith("uk-UA-")


def test_cache_key_v3_includes_lang_backend_length_scale():
    from engines.tts_cache import tts_cache_key

    k1 = tts_cache_key(
        "Привіт", "mykyta", engine_id="tts_uk", lang="uk", length_scale="1.05"
    )
    k2 = tts_cache_key(
        "Привіт", "mykyta", engine_id="tts_uk", lang="uk", length_scale="1.10"
    )
    k3 = tts_cache_key(
        "Привіт", "uk-UA-OstapNeural", engine_id="edge-offline", lang="uk"
    )
    assert k1 != k2
    assert k1 != k3
    # Incomplete voice must miss on lookup
    from engines.tts_cache import lookup_tts_cache

    assert lookup_tts_cache("Привіт", "", engine_id="tts_uk", lang="uk") is None


def test_apply_tts_result_absolutizes(tmp_path):
    from engines.pipeline_integrity.tts_segment_fields import apply_tts_synthesis_result
    from pydub import AudioSegment

    wav = tmp_path / "seg.wav"
    AudioSegment.silent(duration=500).export(str(wav), format="wav")
    seg: dict = {}
    apply_tts_synthesis_result(
        seg, tts_text="Привіт", tts_file_path=str(wav), playback_duration=500
    )
    assert Path(seg["file"]).is_absolute()
    assert Path(seg["file"]).is_file()
    assert seg["tts_ms"] == 500


def test_stamp_tts_backend_meta_sets_language():
    from engines.tts_backends import stamp_tts_backend_meta

    seg = {"final_tts_text": "Привіт світе як справи"}
    stamp_tts_backend_meta(
        seg, engine_id="tts_uk", voice="mykyta", language="uk"
    )
    assert seg["tts_language"] == "uk"
    assert seg["tts_voice"] == "mykyta"
    assert seg["tts_backend"]
    assert float(seg.get("cyrillic_ratio") or 0) >= 0.55


def test_census_degraded_when_missing_without_pads():
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    block = _build_openddf_tts_pipeline_block(
        {
            "segments_data": [
                {"index": 0, "text": "a", "file": None, "tts_ms": 0},
                {"index": 1, "text": "b", "file": None, "tts_ms": 0},
            ]
        }
    )
    assert block["audio_missing"] == 2
    assert block["final_status"] == "degraded"
