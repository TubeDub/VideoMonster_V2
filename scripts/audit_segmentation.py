"""Audit STT segmentation + timing_fit stress (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.segment_merger import merge_stt_segments
from engines.timing_fit import fit_segment_audio, _gentle_atempo_factor
from pydub import AudioSegment
import tempfile


def test_merge_reduces_micro_segments():
    segs = ["Hello", "world", "This is a longer phrase.", "Yes"]
    timing = [
        {"start": 0, "end": 800},
        {"start": 900, "end": 1600},
        {"start": 5000, "end": 9000},
        {"start": 9100, "end": 9800},
    ]
    merged, mt = merge_stt_segments(segs, timing)
    assert len(merged) < len(segs)
    assert len(merged) == len(mt)
    assert merged[0].startswith("Hello")


def test_fit_no_speedup_by_default():
    """TZ №2: без allow_atempo голос не ускоряется."""
    work = Path(tempfile.mkdtemp())
    src = work / "long.wav"
    AudioSegment.silent(duration=5000).export(src, format="wav")
    fitted, meta = fit_segment_audio(
        str(src), 0, 2000, next_start=3000, work_dir=work, allow_atempo=False
    )
    assert meta["atempo"] == 1.0
    assert "atempo" not in meta.get("strategy", "")


def test_fit_gentle_atempo_when_allowed():
    work = Path(tempfile.mkdtemp())
    src = work / "long.wav"
    AudioSegment.silent(duration=5000).export(src, format="wav")
    fitted, meta = fit_segment_audio(
        str(src), 0, 2000, next_start=3000, work_dir=work, allow_atempo=True
    )
    assert meta["atempo"] <= 1.19
    assert meta["atempo"] > 1.0



def test_gentle_atempo_curve():
    assert _gentle_atempo_factor(1.0) == 1.0
    assert _gentle_atempo_factor(1.04) <= 1.05
    assert _gentle_atempo_factor(1.25) <= 1.18


def main() -> int:
    test_merge_reduces_micro_segments()
    test_gentle_atempo_curve()
    test_fit_no_speedup_by_default()
    test_fit_gentle_atempo_when_allowed()
    print("segmentation audit OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
