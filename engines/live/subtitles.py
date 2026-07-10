"""Live subtitle cue emitter (WebVTT fragments)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SubtitleCue:
    start_ms: int
    end_ms: int
    text: str

    def to_vtt(self) -> str:
        def _ts(ms: int) -> str:
            ms = max(0, int(ms))
            h = ms // 3600000
            ms %= 3600000
            m = ms // 60000
            ms %= 60000
            s = ms // 1000
            ms %= 1000
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

        return f"{_ts(self.start_ms)} --> {_ts(self.end_ms)}\n{self.text}\n"


def cues_to_webvtt(cues: list[SubtitleCue]) -> str:
    lines = ["WEBVTT", ""]
    for i, c in enumerate(cues):
        lines.append(str(i + 1))
        lines.append(c.to_vtt())
    return "\n".join(lines)
