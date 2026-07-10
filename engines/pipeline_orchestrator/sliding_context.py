"""Sliding context window for LLM adaptation — prev / current / next segments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class SlidingContext:
    index: int
    previous: str
    current: str
    next_segment: str
    window_before: str
    window_after: str

    def as_prompt_block(self, *, lang: str = "ru") -> str:
        if lang == "en":
            return (
                f"Previous: {self.previous or '—'}\n"
                f"Current: {self.current}\n"
                f"Next: {self.next_segment or '—'}"
            )
        return (
            f"Предыдущий: {self.previous or '—'}\n"
            f"Текущий: {self.current}\n"
            f"Следующий: {self.next_segment or '—'}"
        )


def build_sliding_context(
    index: int,
    segments: Sequence[str],
    *,
    window: int = 1,
) -> SlidingContext:
    """Build prev/current/next context for segment *index*."""
    n = len(segments)
    prev_parts: list[str] = []
    next_parts: list[str] = []
    for w in range(window, 0, -1):
        pi = index - w
        if 0 <= pi < n:
            t = str(segments[pi] or "").strip()
            if t:
                prev_parts.append(t)
    for w in range(1, window + 1):
        ni = index + w
        if 0 <= ni < n:
            t = str(segments[ni] or "").strip()
            if t:
                next_parts.append(t)
    prev_one = str(segments[index - 1] or "").strip() if index > 0 else ""
    next_one = str(segments[index + 1] or "").strip() if index + 1 < n else ""
    return SlidingContext(
        index=index,
        previous=prev_one,
        current=str(segments[index] or "").strip(),
        next_segment=next_one,
        window_before=" ".join(prev_parts).strip(),
        window_after=" ".join(next_parts).strip(),
    )
