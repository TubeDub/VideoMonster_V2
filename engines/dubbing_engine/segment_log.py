"""
Segment-level audit log — 12 fields per ТЗ §12.

Writes:
  output/dev/dubbing_engine/segment_log_<task_id>.tsv   (tab-separated, Excel-friendly)
  output/dev/dubbing_engine/segment_log_latest.tsv      (always the latest run)
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.segment_log")

# ── 12-field schema ────────────────────────────────────────────────────────────

FIELDS = (
    "segment_id",
    "original_text",
    "translation",
    "adapted_text",
    "text_sent_to_tts",
    "predicted_duration_ms",
    "final_duration_ms",
    "strategy",
    "merge_status",
    "pause_duration_ms",
    "validation_passed",
    "validation_notes",
)


@dataclass
class SegmentLogEntry:
    """One row of the segment audit log."""
    segment_id: int = 0
    original_text: str = ""          # Whisper / source language
    translation: str = ""            # Machine translation
    adapted_text: str = ""           # After DubbingEngine adaptation
    text_sent_to_tts: str = ""       # Final text that TTS receives (= adapted_text)
    predicted_duration_ms: int = 0
    final_duration_ms: int = 0       # Actual TTS audio duration
    strategy: str = "direct"         # direct | adapted | video_adapt | merge_next | skip_tts
    merge_status: str = ""           # "merged_with_next=N" | "standalone"
    pause_duration_ms: int = 120     # natural post-sentence pause
    validation_passed: bool = True
    validation_notes: str = ""

    def to_row(self) -> dict[str, str]:
        return {
            "segment_id": str(self.segment_id),
            "original_text": self.original_text[:200],
            "translation": self.translation[:200],
            "adapted_text": self.adapted_text[:200],
            "text_sent_to_tts": self.text_sent_to_tts[:200],
            "predicted_duration_ms": str(self.predicted_duration_ms),
            "final_duration_ms": str(self.final_duration_ms),
            "strategy": self.strategy,
            "merge_status": self.merge_status or "standalone",
            "pause_duration_ms": str(self.pause_duration_ms),
            "validation_passed": "YES" if self.validation_passed else "NO",
            "validation_notes": self.validation_notes[:300],
        }


class SegmentLog:
    """Collects entries during a pipeline run and writes them at the end."""

    def __init__(self, task_id: str = "", app_dir: Path | None = None) -> None:
        self.task_id = task_id
        self.app_dir = app_dir or Path(".")
        self._entries: list[SegmentLogEntry] = []

    def add(self, entry: SegmentLogEntry) -> None:
        self._entries.append(entry)

    def add_from_engine_results(
        self,
        results: list[Any],
        source_hints: list[str] | None = None,
        translated_segments: list[str] | None = None,
        final_durations: dict[int, int] | None = None,
        merge_map: dict[int, int] | None = None,
    ) -> None:
        """Populate log from DubbingResult list."""
        for r in results:
            i = r.index
            entry = SegmentLogEntry(
                segment_id=i,
                original_text=r.original_text or (source_hints[i] if source_hints and i < len(source_hints) else ""),
                translation=r.input_text,
                adapted_text=r.output_text,
                text_sent_to_tts=r.output_text,
                predicted_duration_ms=r.predicted_ms,
                final_duration_ms=(final_durations or {}).get(i, 0),
                strategy=r.recommended_strategy,
                merge_status=(
                    f"merged_with_next={merge_map[i]}" if merge_map and i in merge_map
                    else "standalone"
                ),
                pause_duration_ms=r.natural_pause_ms,
                validation_passed=r.passed_validation,
                validation_notes="; ".join(r.validation_notes)[:300],
            )
            self._entries.append(entry)

    def write(self) -> Path | None:
        """Write TSV log. Returns path or None on error."""
        if not self._entries:
            return None
        try:
            log_dir = self.app_dir / "output" / "dev" / "dubbing_engine"
            log_dir.mkdir(parents=True, exist_ok=True)

            buf = io.StringIO()
            writer = csv.DictWriter(
                buf, fieldnames=list(FIELDS), delimiter="\t", extrasaction="ignore"
            )
            writer.writeheader()
            for entry in self._entries:
                writer.writerow(entry.to_row())

            content = buf.getvalue()

            fname = f"segment_log_{self.task_id or 'latest'}.tsv"
            (log_dir / fname).write_text(content, encoding="utf-8")
            (log_dir / "segment_log_latest.tsv").write_text(content, encoding="utf-8")

            logger.info(
                "[SegmentLog] wrote %d entries → %s/%s",
                len(self._entries), log_dir, fname,
            )
            return log_dir / fname
        except Exception as exc:
            logger.debug("[SegmentLog] write failed: %s", exc)
            return None

    def summary(self) -> dict[str, Any]:
        total = len(self._entries)
        passed = sum(1 for e in self._entries if e.validation_passed)
        adapted = sum(1 for e in self._entries if e.strategy not in ("direct", "skip_tts"))
        skipped = sum(1 for e in self._entries if e.strategy == "skip_tts")
        return {
            "total": total,
            "passed": passed,
            "adapted": adapted,
            "skipped": skipped,
            "strategies": {
                s: sum(1 for e in self._entries if e.strategy == s)
                for s in ("direct", "adapted", "video_adapt", "merge_next",
                           "skip_tts", "delay_start")
            },
        }
