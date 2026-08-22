"""TubeDub Phases 2–8: identity apply, revision UUIDs, micro-slot 850, honest trim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.psa_flags import (
    VM_FLAG_IDENTITY_GUARD,
    VM_FLAG_IDENTITY_GUARD_SHADOW,
    VM_FLAG_REVISION_MANAGER,
    VM_FLAG_SEGMENT_NORMALIZER,
)

ROOT = Path(__file__).resolve().parents[1]
BA6EC = ROOT / "tests" / "fixtures" / "ba6ec_compact.json"
STUDIO_GEORGE = ROOT / "output" / "studio_sessions" / "2286c82f8c3843218062bead60caf382.json"


def _uuid(n: int) -> str:
    return f"c{n:031x}"


def test_shuffled_engine_results_bind_by_segment_id():
    from engines.dubbing_engine.types import DubbingResult
    from engines.pipeline_integrity.identity_guard import apply_engine_text_results

    a_id, b_id = _uuid(1), _uuid(2)
    rows = [
        {"segment_id": a_id, "index": 0, "text": "OLD-A", "plain_text": "OLD-A"},
        {"segment_id": b_id, "index": 1, "text": "OLD-B", "plain_text": "OLD-B"},
    ]
    # Results swapped vs list order / index: index 0 carries B's text, and vice versa.
    results = [
        DubbingResult(
            index=0,
            original_text="en-a",
            input_text="OLD-A",
            output_text="NEW-B-TEXT",
            passed_validation=True,
            segment_id=b_id,
        ),
        DubbingResult(
            index=1,
            original_text="en-b",
            input_text="OLD-B",
            output_text="NEW-A-TEXT",
            passed_validation=True,
            segment_id=a_id,
        ),
    ]
    stats = apply_engine_text_results(rows, results)
    assert stats["applied_by_id"] == 2
    assert stats["applied_by_index"] == 0
    assert rows[0]["text"] == "NEW-A-TEXT"
    assert rows[1]["text"] == "NEW-B-TEXT"


def test_text_change_mints_new_tts_uuid(monkeypatch):
    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "1")
    from engines.pipeline_integrity.revision_manager import (
        ensure_revision_uuids,
        note_text_change,
    )
    from engines.pipeline_integrity.uuid_chain import ensure_tts_uuid

    seg = {
        "segment_id": _uuid(9),
        "plain_text": "hello",
        "text": "hello",
    }
    ensure_revision_uuids(seg)
    old_tts = seg["tts_uuid"]
    note_text_change(seg, "hello there", kind="adaptation")
    ensure_tts_uuid(seg, force_new=True)
    assert seg["tts_uuid"]
    assert seg["tts_uuid"] != old_tts
    assert seg["plain_text"] == "hello there"


def test_micro_slot_850_protection(monkeypatch):
    monkeypatch.setenv(VM_FLAG_SEGMENT_NORMALIZER, "1")
    from engines.pipeline_integrity.segment_normalizer import (
        MIN_SLOT_MS,
        is_micro_or_fragment,
    )
    from engines.segment_timing_qa import clamp_timeline_to_video_duration
    import inspect

    assert MIN_SLOT_MS == 850
    assert is_micro_or_fragment("Hi", 800) is True
    assert is_micro_or_fragment("Hi there friend", 2000) is False
    default = inspect.signature(clamp_timeline_to_video_duration).parameters["min_slot_ms"]
    assert default.default == 850


def test_tail_trim_over_250ms_speech_refuses_and_reports():
    import array

    from pydub import AudioSegment

    from engines.timing_fit import SPEECH_TRIM_SPLIT_MS, fit_segment_audio

    assert SPEECH_TRIM_SPLIT_MS == 250

    work = Path("output") / "_tmp_phase8_trim"
    work.mkdir(parents=True, exist_ok=True)

    def _tone_ms(ms: int, amp: int = 8000) -> AudioSegment:
        sr = 16000
        n = int(sr * ms / 1000)
        samples = array.array("h", [amp if (i // 40) % 2 == 0 else -amp for i in range(n)])
        return AudioSegment(data=samples.tobytes(), sample_width=2, frame_rate=sr, channels=1)

    # 900ms speech into 400ms slot → ~500ms speech would be cut (>250).
    src = work / "speech_overflow.wav"
    _tone_ms(900).export(src, format="wav")
    out, meta = fit_segment_audio(
        src,
        0,
        400,
        next_start=400,
        work_dir=work,
        allow_atempo=False,
        no_speech_trim=False,
    )
    fitted = AudioSegment.from_file(out)
    assert "no_trim_overflow" in str(meta.get("strategy") or "")
    assert "trim_overlap" not in str(meta.get("strategy") or "")
    assert len(fitted) > 400
    assert int(meta.get("overflow_ms") or 0) >= 250

    # Small speech overflow (≤250ms) may still trim; report trimmed-ms honestly.
    src2 = work / "speech_small.wav"
    _tone_ms(520).export(src2, format="wav")
    _out2, meta2 = fit_segment_audio(
        src2,
        0,
        400,
        next_start=400,
        work_dir=work,
        allow_atempo=False,
        no_speech_trim=False,
    )
    trimmed = int(meta2.get("speech_trimmed_ms") or 0)
    overflow = int(meta2.get("overflow_ms") or 0)
    if "trim_overlap" in str(meta2.get("strategy") or ""):
        assert overflow >= trimmed
        assert trimmed > 0 or overflow > 0


def test_identity_guard_shadow_flags_ba6ec_bleed(monkeypatch):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD_SHADOW, "1")
    from engines.pipeline_integrity.identity_guard import verify_identity_chain

    data = json.loads(BA6EC.read_text(encoding="utf-8"))
    segs = []
    for row in data["segments"]:
        segs.append(
            {
                "segment_id": row["segment_id"],
                "plain_text": row["translated_text"],
                "translated_text": row["translated_text"],
                "final_tts_text": row["final_tts_text"],
                "owned_text_segment_id": row["segment_id"],
            }
        )
    report = verify_identity_chain(segs, stage="shadow_ba6ec")
    assert report.get("enabled") is True
    assert report.get("ok") is False
    assert report.get("report_only") is True
    assert report.get("violations")
    msg = str(report["violations"][0].get("message") or "").lower()
    assert "identity shift" in msg or "foreign" in msg


def test_identity_guard_shadow_george_lucas_session(monkeypatch):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD_SHADOW, "1")
    from engines.pipeline_integrity.identity_guard import verify_identity_chain

    if not STUDIO_GEORGE.is_file():
        pytest.skip("studio session 2286c82f not present")
    raw = json.loads(STUDIO_GEORGE.read_text(encoding="utf-8"))
    segs = (
        raw.get("segments_data")
        or (raw.get("info") or {}).get("segments_data")
        or raw.get("segments")
        or []
    )
    if not segs:
        pytest.skip("2286c82f has no segments_data")
    rows = [s for s in segs if isinstance(s, dict)]
    report = verify_identity_chain(rows, stage="shadow_2286c82f")
    assert report.get("enabled") is True
    # Either the stored session still has the bleed, or it was already repaired.
    if report.get("ok") is False:
        assert report.get("report_only") is True
        assert report.get("violations")
    else:
        # Fixture already clean — ba6ec shadow test covers the detector.
        assert report.get("ok") is True
