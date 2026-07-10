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

    def register(self, segment_id: str, path: Path) -> ArtifactRecord:
        t0 = time.perf_counter()
        if not path.is_file():
            raise FileNotFoundError(str(path))
        name = path.name
        digest = sha256_file(path)
        size = path.stat().st_size
        existing_owner = self.file_to_segment.get(name)
        if existing_owner and existing_owner != segment_id:
            raise ValueError(f"file {name!r} already bound to {existing_owner}")
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
