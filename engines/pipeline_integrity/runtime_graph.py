"""Runtime dependency graph + break-point reporting — P3.1 §15."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


GRAPH_NODES = (
    "Segment",
    "Translation",
    "TTS",
    "Scheduler",
    "Merge",
    "Studio",
    "Export",
)


@dataclass
class GraphEdge:
    src: str
    dst: str
    ok: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "ok": self.ok, "detail": self.detail}


@dataclass
class RuntimeGraph:
    edges: list[GraphEdge] = field(default_factory=list)
    broken_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": list(GRAPH_NODES),
            "edges": [e.to_dict() for e in self.edges],
            "broken_at": self.broken_at,
        }


def build_runtime_graph(
    info: dict[str, Any],
    *,
    stage_failures: dict[str, str] | None = None,
) -> RuntimeGraph:
    failures = stage_failures or {}
    pairs = list(zip(GRAPH_NODES, GRAPH_NODES[1:]))
    edges: list[GraphEdge] = []
    broken = ""
    # Map lifecycle / pipeline hints
    segments = list(info.get("segments_data") or [])
    has_text = any(
        str(s.get("translated_text") or s.get("text") or "").strip()
        for s in segments
        if isinstance(s, dict)
    )
    has_tts = any(
        (s.get("file") or s.get("tts_file_path") or s.get("tts_uuid"))
        for s in segments
        if isinstance(s, dict)
    )
    has_timing = any(
        s.get("start_ms") is not None or s.get("start_time_ms") is not None
        for s in segments
        if isinstance(s, dict)
    )
    has_merge = bool(
        info.get("final_audio_path")
        or info.get("merged_track")
        or info.get("mux_output")
    )
    state = str(info.get("pipeline_state") or "")
    hints = {
        "Segment→Translation": has_text,
        "Translation→TTS": has_tts,
        "TTS→Scheduler": has_timing and has_tts,
        "Scheduler→Merge": has_timing,
        "Merge→Studio": has_merge or state in {"HANDOFF", "EXPORTED", "MERGED"},
        "Studio→Export": state in {"HANDOFF", "EXPORTED"},
    }
    for src, dst in pairs:
        key = f"{src}→{dst}"
        fail = failures.get(dst) or failures.get(key) or failures.get(src)
        ok = hints.get(key, True) and not fail
        edges.append(GraphEdge(src=src, dst=dst, ok=ok, detail=str(fail or "")))
        if not ok and not broken:
            broken = key
    return RuntimeGraph(edges=edges, broken_at=broken)
