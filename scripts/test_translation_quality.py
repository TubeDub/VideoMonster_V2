"""STT + translate comparison → output/quality_test.txt"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEARCH_ROOTS = [
    Path(r"c:\Users\serhii\Desktop\DDU v18.1.5.4"),
    Path(r"c:\Users\serhii\Desktop"),
    ROOT / "uploads",
    ROOT / "output",
]
VIDEO_ID = "video_c262b02ece"
OUT = ROOT / "output" / "quality_test.txt"


def find_test_video() -> tuple[Path | None, list[Path]]:
    """Prefer original (no _OUTPUT_) over prior dub output."""
    hits: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.mp4"):
            if VIDEO_ID in path.name.lower():
                hits.append(path)

    if not hits:
        return None, []

    hits.sort(key=lambda p: ("_output_" in p.name.lower(), p.name.lower()))
    original = next((p for p in hits if "_OUTPUT_" not in p.name.upper()), None)
    return original or hits[0], hits


def resolve_video() -> Path | None:
    if len(sys.argv) > 1:
        arg = Path(sys.argv[1])
        return arg if arg.exists() else None
    chosen, _ = find_test_video()
    return chosen


def extract_audio(video: Path, audio: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
            "-vn", "-acodec", "mp3", "-ar", "16000", "-ac", "1", str(audio),
        ],
        check=True,
        capture_output=True,
    )


def run_mock_report(lines: list[str]) -> str:
    """Offline fallback when STT/network unavailable."""
    from engines.translation import translate_segments
    from engines.translation_naturalizer import (
        dedupe_consecutive_similar,
        naturalize_ru,
        polish_lines,
        translate_segments_natural,
    )

    timing = [{"start": i * 2000, "end": (i + 1) * 2000} for i in range(len(lines))]
    per = translate_segments(lines, "en", "ru")
    natural = translate_segments_natural(lines, timing, "en", "ru")

    # simulate per-segment robotic without API: repeat subject
    robotic = []
    for ln in lines:
        robotic.append(f"Коза {ln.lower()}" if "goat" in ln.lower() else ln)

    polished = polish_lines(
        [naturalize_ru(r, robotic[i - 1] if i else None) for i, r in enumerate(robotic)],
        tgt_lang="ru",
    )
    deduped = dedupe_consecutive_similar(polished)

    buf = [
        "=== MOCK MODE (no STT/video) ===",
        f"Time: {datetime.now().isoformat()}",
        "",
    ]
    for i, src in enumerate(lines):
        buf.append(f"#{i+1} SRC: {src}")
        buf.append(f"    PER-SEG (robotic sim): {robotic[i]}")
        buf.append(f"    NATURAL: {natural[i] if i < len(natural) else ''}")
        buf.append(f"    POLISHED: {polished[i] if i < len(polished) else ''}")
        buf.append("")
    buf.append("Deduped sample: " + " | ".join(deduped[:5]))
    return "\n".join(buf)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    video, all_hits = find_test_video()
    VIDEO = resolve_video()

    buf: list[str] = [
        "VideoMonster V2 — Translation Quality Test",
        f"Time: {datetime.now().isoformat()}",
        f"Video: {VIDEO or '(not found)'}",
        "",
        f"Search hits ({len(all_hits)}):",
    ]
    for hit in all_hits:
        tag = "ORIGINAL" if "_OUTPUT_" not in hit.name.upper() else "OUTPUT"
        buf.append(f"  [{tag}] {hit}")
    buf.append("")
    if video and "_OUTPUT_" not in video.name.upper():
        buf.append("Original video found: YES")
    else:
        buf.append("Original video found: NO")
        if all_hits:
            buf.append("Only OUTPUT or no source without _OUTPUT_ in search paths.")
    buf.append("")

    if not VIDEO or not VIDEO.exists():
        buf.append("VIDEO NOT FOUND — running mock samples")
        buf.append("")
        sample = [
            "The goat was walking through the field",
            "The goat was chewing grass",
            "The goat looked at the camera",
            "Then it continued eating",
        ]
        buf.append(run_mock_report(sample))
        OUT.write_text("\n".join(buf), encoding="utf-8")
        print(f"Written (mock): {OUT}")
        return 0

    try:
        from engines.stt_engine import transcribe
        from engines.translation import translate_segments
        from engines.translation_naturalizer import translate_segments_natural

        with tempfile.TemporaryDirectory() as td:
            audio = Path(td) / "extract.mp3"
            buf.append("Step: extract audio")
            extract_audio(VIDEO, audio)

            buf.append("Step: STT (tiny)")
            source_text, _, timing_map, lang = transcribe(str(audio), model_size="tiny")
            segments = [ln.strip() for ln in source_text.splitlines() if ln.strip()]
            src = lang if lang and lang != "unknown" else "en"

            buf.append(f"Detected lang: {lang}, segments: {len(segments)}")
            if "_OUTPUT_" in VIDEO.name.upper():
                buf.append(
                    "WARNING: input is prior OUTPUT file — double-voice risk if re-dubbing mixed audio"
                )
            buf.append("")

            per_seg = translate_segments(segments, src, "ru")
            natural = translate_segments_natural(segments, timing_map, src, "ru")

            buf.append("=== BEFORE (per-segment) vs AFTER (batch+natural) ===")
            for i in range(min(len(segments), 25)):
                buf.append(f"#{i+1}")
                buf.append(f"  SRC: {segments[i]}")
                buf.append(f"  PER-SEG: {per_seg[i] if i < len(per_seg) else ''}")
                buf.append(f"  NATURAL: {natural[i] if i < len(natural) else ''}")
                buf.append("")

    except Exception as e:
        buf.append(f"STT/translate failed: {e}")
        buf.append("")
        buf.append(run_mock_report([
            "The goat was walking",
            "The goat was chewing",
            "The goat ate grass slowly",
        ]))

    OUT.write_text("\n".join(buf), encoding="utf-8")
    print(f"Written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
