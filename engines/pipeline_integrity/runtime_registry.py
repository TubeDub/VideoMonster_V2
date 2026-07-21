"""Runtime Registry — P3.1 §13 single source of truth for paths/state/ownership."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.artifact_registry import sha256_file
from engines.pipeline_integrity.tts_artifact_lifecycle import (
    TTSLifecycleState,
    get_tts_lifecycle,
)
from engines.pipeline_integrity.uuid_chain import ensure_all_uuids
from engines.pipeline_integrity.wav_ownership import get_wav_owner, stamp_wav_owner


@dataclass
class RuntimeRecord:
    segment_uuid: str
    tts_uuid: str = ""
    audio_uuid: str = ""
    translation_uuid: str = ""
    merge_uuid: str = ""
    state: str = TTSLifecycleState.CREATED.value
    owner: str = "TTS Engine"
    path: str = ""
    hash: str = ""
    size_bytes: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_modified_by: str = ""
    last_deleted_by: str = ""
    ref_count: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeRegistry:
    """Durable UUID→path/state registry. Filenames are display-only."""

    def __init__(self, task_id: str = "") -> None:
        self.task_id = task_id
        self._lock = threading.RLock()
        self.records: dict[str, RuntimeRecord] = {}
        self.events: list[dict[str, Any]] = []

    def _log(self, event: str, **kwargs: Any) -> None:
        self.events.append({"ts": time.time(), "event": event, **kwargs})
        if len(self.events) > 2000:
            self.events = self.events[-1000:]

    def upsert_from_segment(
        self,
        seg: dict[str, Any],
        *,
        path: Path | str | None = None,
        actor: str = "registry",
        compute_hash: bool = True,
    ) -> RuntimeRecord:
        ids = ensure_all_uuids(seg)
        suid = ids["segment_uuid"]
        stamp_wav_owner(seg)
        p = Path(path) if path else Path(str(seg.get("tts_file_path") or seg.get("file") or ""))
        digest = ""
        size = 0
        if p.is_file():
            size = p.stat().st_size
            if compute_hash:
                digest = sha256_file(p)
            abs_path = str(p.resolve())
        else:
            abs_path = str(p) if str(p) else str(seg.get("tts_file_path") or seg.get("file") or "")
        now = time.time()
        with self._lock:
            existing = self.records.get(suid)
            rec = RuntimeRecord(
                segment_uuid=suid,
                tts_uuid=ids["tts_uuid"],
                audio_uuid=ids["audio_uuid"],
                translation_uuid=ids["translation_uuid"],
                merge_uuid=ids["merge_uuid"],
                state=get_tts_lifecycle(seg).value,
                owner=get_wav_owner(seg).value,
                path=abs_path,
                hash=digest or (existing.hash if existing else ""),
                size_bytes=size or (existing.size_bytes if existing else 0),
                created_at=existing.created_at if existing else now,
                updated_at=now,
                last_modified_by=actor,
                last_deleted_by=existing.last_deleted_by if existing else "",
                ref_count=existing.ref_count if existing else 1,
                history=list(existing.history if existing else []),
            )
            rec.history.append(
                {
                    "event": "upsert",
                    "actor": actor,
                    "path": abs_path,
                    "state": rec.state,
                    "ts": now,
                }
            )
            rec.history = rec.history[-40:]
            self.records[suid] = rec
            # Mirror onto segment for consumers
            seg["runtime_registry_path"] = abs_path
            if digest:
                seg["audio_sha256"] = digest
            self._log("upsert", segment_uuid=suid, path=abs_path, actor=actor)
            return rec

    def get(self, segment_uuid: str) -> RuntimeRecord | None:
        with self._lock:
            return self.records.get(segment_uuid)

    def get_by_tts_uuid(self, tts_uuid: str) -> RuntimeRecord | None:
        with self._lock:
            for rec in self.records.values():
                if rec.tts_uuid == tts_uuid:
                    return rec
        return None

    def resolve_path(self, segment_uuid: str) -> Path | None:
        rec = self.get(segment_uuid)
        if not rec or not rec.path:
            return None
        p = Path(rec.path)
        return p if p.is_file() else None

    def mark_deleted(self, segment_uuid: str, *, actor: str) -> None:
        with self._lock:
            rec = self.records.get(segment_uuid)
            if not rec:
                return
            rec.last_deleted_by = actor
            rec.updated_at = time.time()
            rec.history.append({"event": "deleted", "actor": actor, "ts": rec.updated_at})
            self._log("deleted", segment_uuid=segment_uuid, actor=actor)

    def sync_project(self, segments: list[dict[str, Any]], *, actor: str = "sync") -> int:
        n = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            self.upsert_from_segment(seg, actor=actor)
            n += 1
        return n

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "task_id": self.task_id,
                "records": [r.to_dict() for r in self.records.values()],
                "events": list(self.events[-200:]),
            }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "RuntimeRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        reg = cls(task_id=str(data.get("task_id") or ""))
        for raw in data.get("records") or []:
            rec = RuntimeRecord(**{k: raw[k] for k in RuntimeRecord.__dataclass_fields__ if k in raw})
            reg.records[rec.segment_uuid] = rec
        reg.events = list(data.get("events") or [])
        return reg


def get_or_create_registry(info: dict[str, Any]) -> RuntimeRegistry:
    existing = info.get("_runtime_registry")
    if isinstance(existing, RuntimeRegistry):
        return existing
    reg = RuntimeRegistry(task_id=str(info.get("task_id") or ""))
    path = info.get("runtime_registry_path")
    if path and Path(str(path)).is_file():
        try:
            reg = RuntimeRegistry.load(Path(str(path)))
        except Exception:
            pass
    info["_runtime_registry"] = reg
    return reg
