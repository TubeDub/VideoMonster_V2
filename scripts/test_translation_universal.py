"""
Universal translation quality test + A/B compare (internal).
Usage:
  python scripts/test_translation_universal.py
  python scripts/test_translation_universal.py path/to/video.mp4
  python scripts/test_translation_universal.py --langs en,de,fr
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "output" / "dev" / "translation_compare_report.txt"
QUALITY_LOG = ROOT / "output" / "dev" / "translation_quality.log"

SAMPLE_EN = [
    "The bank of the river was muddy after the rain.",
    "She went to the bank to deposit her paycheck.",
    "This movie is about a family trying to find their way home.",
]


def run_sample_compare() -> str:
    from engines.translation import translate_segments
    from engines.translation_compare import compare_strategies, score_translation_quality
    from engines.translation_naturalizer import translate_segments_natural

    timing = [{"start": i * 2500, "end": (i + 1) * 2500} for i in range(len(SAMPLE_EN))]
    targets = ["ru", "uk", "de", "fr", "es"]

    lines = [
        "=== Universal Translation Compare (sample) ===",
        f"Time: {datetime.now().isoformat()}",
        "",
    ]

    for tgt in targets:
        strategies = {
            "per_segment": lambda t=tgt: translate_segments(SAMPLE_EN, "en", t),
            "universal_pipeline": lambda t=tgt: translate_segments_natural(
                SAMPLE_EN, timing, "en", t, task_id="compare-sample"
            ),
        }
        from engines.translation_compare import compare_strategies

        ranked = compare_strategies(SAMPLE_EN, timing, "en", tgt, strategies)
        lines.append(f"--- en → {tgt} ---")
        for cand in ranked:
            lines.append(f"  [{cand.score:.1f}] {cand.name} {cand.details}")
            for i, seg in enumerate(cand.segments[:3]):
                lines.append(f"    #{i+1} {seg[:120]}")
        best = ranked[0] if ranked else None
        if best:
            lines.append(f"  WINNER: {best.name} (score={best.score:.1f})")
        lines.append("")

    return "\n".join(lines)


def run_live_stt(video: Path) -> str:
    import subprocess
    import tempfile

    from engines.stt_engine import transcribe
    from engines.translation import translate_segments
    from engines.translation_compare import compare_strategies
    from engines.translation_naturalizer import translate_segments_natural

    lines = [
        "=== Universal Translation Compare (live STT) ===",
        f"Video: {video}",
        f"Time: {datetime.now().isoformat()}",
        "",
    ]

    with tempfile.TemporaryDirectory() as td:
        audio = Path(td) / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video),
                "-vn", "-acodec", "mp3", "-ar", "16000", "-ac", "1", str(audio),
            ],
            check=True,
            capture_output=True,
        )
        source_text, _, timing_map, lang = transcribe(str(audio), model_size="tiny")
        segments = [ln.strip() for ln in source_text.splitlines() if ln.strip()]
        src = lang if lang and lang != "unknown" else "en"

    lines.append(f"Detected: {src}, segments={len(segments)}")
    lines.append("")

    tgt = "ru"
    strategies = {
        "per_segment": lambda: translate_segments(segments, src, tgt),
        "universal_pipeline": lambda: translate_segments_natural(
            segments, timing_map, src, tgt, task_id="compare-live"
        ),
    }
    ranked = compare_strategies(segments, timing_map, src, tgt, strategies)
    for cand in ranked:
        lines.append(f"[{cand.score:.1f}] {cand.name} — {cand.details}")
    lines.append("")
    lines.append("=== First 10 segments trace ===")
    best = ranked[0].segments if ranked else []
    per = strategies["per_segment"]()
    uni = strategies["universal_pipeline"]()
    for i in range(min(10, len(segments))):
        lines.append(f"#{i+1} WHISPER: {segments[i]}")
        lines.append(f"    PER-SEG:  {per[i] if i < len(per) else ''}")
        lines.append(f"    PIPELINE: {uni[i] if i < len(uni) else ''}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    video_arg = next((a for a in sys.argv[1:] if not a.startswith("-")), None)

    if video_arg and Path(video_arg).exists():
        body = run_live_stt(Path(video_arg))
    else:
        body = run_sample_compare()

    footer = ""
    if QUALITY_LOG.is_file():
        tail = QUALITY_LOG.read_text(encoding="utf-8")[-2000:]
        footer = f"\n\n--- translation_quality.log (tail) ---\n{tail}"

    OUT.write_text(body + footer, encoding="utf-8")
    print(body)
    print(f"\nWritten: {OUT}")
    if QUALITY_LOG.is_file():
        print(f"Quality log: {QUALITY_LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
