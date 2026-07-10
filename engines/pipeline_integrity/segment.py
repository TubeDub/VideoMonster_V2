"""Immutable Segment model — single source of truth (TZ §1.1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping


def new_segment_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Segment:
    """
    Immutable pipeline segment. Mutations produce new instances via `evolve()`.
    `segment_id` is the only cross-stage linker (Index-Free Pipeline).
    """

    segment_id: str
    text: str
    index: int = 0
    file: str | None = None
    fitted_file: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    merged_into_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        sid = str(data.get("segment_id") or "").strip()
        if not sid:
            sid = new_segment_id()
        known = {
            "segment_id",
            "text",
            "index",
            "file",
            "fitted_file",
            "start_ms",
            "end_ms",
            "merged_into_id",
            "merged_into",
        }
        extra = {k: v for k, v in data.items() if k not in known}
        merged_id = data.get("merged_into_id")
        if merged_id is None and data.get("merged_into") is not None:
            extra = dict(extra)
            extra["_legacy_merged_into_index"] = data.get("merged_into")
        return cls(
            segment_id=sid,
            text=str(data.get("text") or "").strip(),
            index=int(data.get("index", 0)),
            file=data.get("file"),
            fitted_file=data.get("fitted_file"),
            start_ms=_optional_int(data.get("start_ms")),
            end_ms=_optional_int(data.get("end_ms")),
            merged_into_id=str(merged_id) if merged_id else None,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "segment_id": self.segment_id,
            "index": self.index,
            "text": self.text,
            "file": self.file,
        }
        if self.fitted_file:
            out["fitted_file"] = self.fitted_file
        if self.start_ms is not None:
            out["start_ms"] = self.start_ms
        if self.end_ms is not None:
            out["end_ms"] = self.end_ms
        if self.merged_into_id:
            out["merged_into_id"] = self.merged_into_id
        legacy_idx = self.extra.get("_legacy_merged_into_index")
        if legacy_idx is not None:
            out["merged_into"] = legacy_idx
        for k, v in self.extra.items():
            if k == "_legacy_merged_into_index":
                continue
            out[k] = v
        return out

    def evolve(self, **changes: Any) -> Segment:
        data = self.to_dict()
        data.update(changes)
        return Segment.from_dict(data)

    @property
    def audio_filename(self) -> str | None:
        name = self.fitted_file or self.file
        return Path(str(name)).name if name else None

    def is_active(self) -> bool:
        return self.merged_into_id is None and self.extra.get("_legacy_merged_into_index") is None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def ensure_segment_ids(segments_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign segment_id to legacy rows missing it (in-place, deterministic order)."""
    seen: set[str] = set()
    for row in segments_data:
        sid = str(row.get("segment_id") or "").strip()
        if not sid or sid in seen:
            sid = new_segment_id()
            while sid in seen:
                sid = new_segment_id()
        row["segment_id"] = sid
        seen.add(sid)
    return segments_data


def segments_by_id(segments_data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(s["segment_id"]): s for s in segments_data if s.get("segment_id")}


def resolve_head_segment(
    seg: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_index: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve merge head via merged_into_id (preferred) or legacy merged_into index."""
    mid = seg.get("merged_into_id")
    if mid and mid in by_id:
        return by_id[mid]
    legacy = seg.get("merged_into")
    if legacy is not None and by_index is not None:
        idx = int(legacy)
        if 0 <= idx < len(by_index):
            return by_index[idx]
    return seg
