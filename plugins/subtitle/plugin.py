"""Builtin subtitle export plugin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.base import BasePlugin
from sdk.core_api import register_exporter


class Plugin(BasePlugin):
    PLUGIN_NAME = "subtitle"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = ["subtitle", "export"]

    def on_init(self) -> None:
        register_exporter("subtitle_srt", self.export_srt, plugin_name=self.PLUGIN_NAME)

    def export_srt(
        self,
        segments: list[dict[str, Any]] | None = None,
        *,
        output_path: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.track_call()
        from engines.subtitle_formats import SubtitleSegment, export_srt

        segs: list[SubtitleSegment] = []
        for i, raw in enumerate(segments or []):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("translated_text") or "").strip()
            if not text:
                continue
            start_ms = int(raw.get("start_ms") or raw.get("start") or 0)
            end_ms = int(raw.get("end_ms") or raw.get("end") or (start_ms + 1000))
            # If values look like seconds (< 10000 and floats), convert
            if start_ms < 500 and isinstance(raw.get("start"), float):
                start_ms = int(float(raw["start"]) * 1000)
            if end_ms < 500 and isinstance(raw.get("end"), float):
                end_ms = int(float(raw["end"]) * 1000)
            segs.append(SubtitleSegment(index=i + 1, start_ms=start_ms, end_ms=end_ms, text=text))

        content = export_srt(segs)
        out = output_path
        if out:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {"ok": True, "srt": content, "segments": len(segs), "output_path": out or None}


def create_plugin() -> Plugin:
    return Plugin()
