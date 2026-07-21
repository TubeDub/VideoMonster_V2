"""Artifact SHA-256 registry — Artifact Integrity Guard (TZ §3.4)."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ArtifactRecord:
    segment_id: str
    filename: str
    sha256: str
    size_bytes: int
    registered_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "registered_at_ms": self.registered_at_ms,
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ArtifactRegistry:
    """Maps segment_id -> artifact and filename -> segment_id (No Audio Reuse)."""

    records: dict[str, ArtifactRecord] = field(default_factory=dict)
    file_to_segment: dict[str, str] = field(default_factory=dict)
    profile_ms: float = 0.0

    def unregister(self, segment_id: str) -> None:
        """Drop a segment binding (archived / superseded parent)."""
        rec = self.records.pop(segment_id, None)
        if rec is None:
            return
        if self.file_to_segment.get(rec.filename) == segment_id:
            self.file_to_segment.pop(rec.filename, None)

    def register(
        self,
        segment_id: str,
        path: Path,
        *,
        active_ids: set[str] | None = None,
        rebind_orphans: bool = False,
    ) -> ArtifactRecord:
        """
        Bind segment_id -> file.

        No-Audio-Reuse: two live segments may not share a filename.
        When ``rebind_orphans`` is set (studio_handoff sync), a file still
        bound to an id outside ``active_ids`` (archived post-reissue parent)
        transfers to the new segment_id.
        """
        t0 = time.perf_counter()
        if not path.is_file():
            raise FileNotFoundError(str(path))
        name = path.name
        digest = sha256_file(path)
        size = path.stat().st_size
        existing_owner = self.file_to_segment.get(name)
        if existing_owner and existing_owner != segment_id:
            if (
                rebind_orphans
                and active_ids is not None
                and existing_owner not in active_ids
            ):
                self.unregister(existing_owner)
            else:
                raise ValueError(f"file {name!r} already bound to {existing_owner}")
        # Segment changed file after re-TTS — drop stale filename binding.
        old_rec = self.records.get(segment_id)
        if old_rec is not None and old_rec.filename != name:
            if self.file_to_segment.get(old_rec.filename) == segment_id:
                self.file_to_segment.pop(old_rec.filename, None)
        rec = ArtifactRecord(
            segment_id=segment_id,
            filename=name,
            sha256=digest,
            size_bytes=size,
        )
        self.records[segment_id] = rec
        self.file_to_segment[name] = segment_id
        self.profile_ms += (time.perf_counter() - t0) * 1000.0
        return rec

    def verify(self, segment_id: str, path: Path) -> bool:
        rec = self.records.get(segment_id)
        if rec is None:
            return False
        if path.name != rec.filename:
            return False
        if not path.is_file():
            return False
        return sha256_file(path) == rec.sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records.values()],
            "profile_ms": round(self.profile_ms, 3),
        }
