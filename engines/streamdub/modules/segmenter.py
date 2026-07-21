"""Smart Segmenter — pause/length/punctuation aware segmentation."""

from __future__ import annotations

import re
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule
from engines.streamdub.types import StreamSegment


class SmartSegmenter(StreamModule):
    module_id = "segmenter"

    def __init__(self) -> None:
        super().__init__()
        self._max_tokens = 80
        self._max_chars = 220

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._max_tokens = int(cfg.get("max_tokens_per_segment") or 80)
        self._max_chars = int(cfg.get("max_chars_per_segment") or self._max_tokens * 3)

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        return True, "ready", {"max_tokens": self._max_tokens}

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            features=["pause_merge", "punctuation_split", "max_length", "intonation_heuristic"],
        )

    def _estimate_tokens(self, text: str) -> int:
        words = re.findall(r"\w+", text, flags=re.UNICODE)
        return max(1, len(words))

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming: list[StreamSegment] = list(payload.get("segments") or [])
        if not incoming:
            return {"segments": []}

        merged: list[StreamSegment] = []
        buf: StreamSegment | None = None

        for seg in incoming:
            if buf is None:
                buf = StreamSegment(
                    index=0,
                    text=seg.text,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                    speaker=seg.speaker,
                    pause_after_ms=seg.pause_after_ms,
                    meta=dict(seg.meta),
                )
                continue

            combined = f"{buf.text} {seg.text}".strip()
            long_pause = seg.pause_after_ms >= 600 or buf.pause_after_ms >= 600
            too_long = (
                self._estimate_tokens(combined) > self._max_tokens
                or len(combined) > self._max_chars
            )
            ends_sentence = bool(re.search(r"[.!?…]\s*$", buf.text))

            if long_pause or too_long or ends_sentence:
                merged.append(buf)
                buf = StreamSegment(
                    index=len(merged),
                    text=seg.text,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                    speaker=seg.speaker,
                    pause_after_ms=seg.pause_after_ms,
                    meta=dict(seg.meta),
                )
            else:
                buf.text = combined
                buf.end_ms = seg.end_ms
                buf.pause_after_ms = seg.pause_after_ms

        if buf is not None:
            merged.append(buf)

        for i, seg in enumerate(merged):
            seg.index = i

        return {"segments": merged, "merged_from": len(incoming), "merged_to": len(merged)}
