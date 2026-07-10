"""Dub Studio project persistence."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from engines.dub_studio.models import (
    DubProject,
    FxSlot,
    SegmentVersion,
    StudioSegment,
    StudioTrack,
    TrackKind,
)

_LOCK = threading.RLock()


class ProjectStore:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self.root = self.app_dir / "projects" / "dub_studio"
        self.index_path = self.app_dir / "data" / "dub_studio_projects.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        d = self.root / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _project_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def save(self, project: DubProject) -> DubProject:
        with _LOCK:
            project.updated_ms = int(time.time() * 1000)
            self._project_file(project.project_id).write_text(
                json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            idx = self._load_index()
            idx[project.project_id] = {
                "project_id": project.project_id,
                "title": project.title,
                "updated_ms": project.updated_ms,
            }
            self.index_path.write_text(
                json.dumps({"projects": idx}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return project

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {}
        try:
            return dict(json.loads(self.index_path.read_text(encoding="utf-8")).get("projects") or {})
        except Exception:
            return {}

    def load(self, project_id: str) -> DubProject | None:
        p = self._project_file(project_id)
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return _parse_project(raw)
        except Exception:
            return None

    def list_projects(self) -> list[dict[str, Any]]:
        return list(self._load_index().values())

    def create_empty(self, *, title: str = "Dub Studio Project") -> DubProject:
        now = int(time.time() * 1000)
        pid = str(uuid.uuid4())
        tracks = [
            StudioTrack(str(uuid.uuid4()), TrackKind.VIDEO.value, "Video"),
            StudioTrack(str(uuid.uuid4()), TrackKind.ORIGINAL.value, "Original Audio"),
            StudioTrack(str(uuid.uuid4()), TrackKind.TTS.value, "Auto Dub"),
            StudioTrack(str(uuid.uuid4()), TrackKind.USER_VOICE.value, "User Voice"),
            StudioTrack(str(uuid.uuid4()), TrackKind.MUSIC.value, "Music"),
            StudioTrack(str(uuid.uuid4()), TrackKind.FX.value, "Effects"),
            StudioTrack(str(uuid.uuid4()), TrackKind.AUX.value, "Aux"),
        ]
        project = DubProject(
            project_id=pid,
            title=title,
            tracks=tracks,
            created_ms=now,
            updated_ms=now,
        )
        return self.save(project)


def _parse_project(raw: dict[str, Any]) -> DubProject:
    tracks = []
    for t in raw.get("tracks") or []:
        fx = [FxSlot(**f) if isinstance(f, dict) else f for f in (t.get("fx_chain") or [])]
        plugins = [FxSlot(**f) if isinstance(f, dict) else f for f in (t.get("plugin_slots") or [])]
        tracks.append(
            StudioTrack(
                track_id=str(t.get("track_id")),
                kind=str(t.get("kind")),
                label=str(t.get("label")),
                muted=bool(t.get("muted", False)),
                solo=bool(t.get("solo", False)),
                volume=float(t.get("volume", 1.0)),
                pan=float(t.get("pan", 0.0)),
                monitor=bool(t.get("monitor", False)),
                record_enabled=bool(t.get("record_enabled", False)),
                fx_chain=fx,
                plugin_slots=plugins or fx,
                clips=list(t.get("clips") or []),
            )
        )
    segments = []
    for s in raw.get("segments") or []:
        versions = [
            SegmentVersion(**v) if isinstance(v, dict) else v for v in (s.get("versions") or [])
        ]
        fx = [FxSlot(**f) if isinstance(f, dict) else f for f in (s.get("fx_chain") or [])]
        segments.append(
            StudioSegment(
                segment_id=str(s.get("segment_id")),
                index=int(s.get("index", 0)),
                text=str(s.get("text") or ""),
                start_ms=int(s.get("start_ms", 0)),
                end_ms=int(s.get("end_ms", 0)),
                hard_anchor_ms=int(s.get("hard_anchor_ms", 0)),
                container_ms=int(s.get("container_ms", 0)),
                tts_ms=int(s.get("tts_ms", 0)),
                stretch_ratio=float(s.get("stretch_ratio", 1.0)),
                container_status=str(s.get("container_status") or "green"),
                emotion=str(s.get("emotion") or "NEUTRAL"),
                emotion_confidence=float(s.get("emotion_confidence", 0)),
                emotion_manual=bool(s.get("emotion_manual", False)),
                tts_params=dict(s.get("tts_params") or {}),
                active_version_id=str(s.get("active_version_id") or ""),
                versions=versions,
                fx_chain=fx,
                meta=dict(s.get("meta") or {}),
            )
        )
    master_fx = [FxSlot(**f) if isinstance(f, dict) else f for f in (raw.get("master_fx") or [])]
    return DubProject(
        project_id=str(raw.get("project_id")),
        title=str(raw.get("title") or ""),
        video_path=str(raw.get("video_path") or ""),
        duration_ms=int(raw.get("duration_ms", 0)),
        tracks=tracks,
        segments=segments,
        master_fx=master_fx,
        created_ms=int(raw.get("created_ms", 0)),
        updated_ms=int(raw.get("updated_ms", 0)),
        meta=dict(raw.get("meta") or {}),
    )
