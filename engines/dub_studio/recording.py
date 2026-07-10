"""User recording — Punch & Roll with auto-sync stub."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PunchRollSession:
    session_id: str
    project_id: str
    segment_id: str
    pre_roll_ms: int = 500
    anchor_ms: int = 0
    status: str = "idle"
    takes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "segment_id": self.segment_id,
            "pre_roll_ms": self.pre_roll_ms,
            "anchor_ms": self.anchor_ms,
            "status": self.status,
            "takes": self.takes,
        }


class RecordingManager:
    """Punch & Roll recording — Stage 2: WebAudio / sounddevice capture."""

    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._dir = self.app_dir / "output" / "dub_studio" / "recordings"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, PunchRollSession] = {}

    def start_punch_roll(
        self,
        *,
        project_id: str,
        segment_id: str,
        anchor_ms: int,
        pre_roll_ms: int = 500,
    ) -> PunchRollSession:
        sid = str(uuid.uuid4())
        sess = PunchRollSession(
            session_id=sid,
            project_id=project_id,
            segment_id=segment_id,
            pre_roll_ms=pre_roll_ms,
            anchor_ms=anchor_ms,
            status="armed",
        )
        self._sessions[sid] = sess
        return sess

    def register_take(
        self,
        session_id: str,
        audio_path: Path,
        *,
        auto_sync: bool = True,
    ) -> dict[str, Any]:
        sess = self._sessions.get(session_id)
        if not sess:
            raise KeyError(session_id)
        take = {
            "take_id": str(uuid.uuid4()),
            "path": str(audio_path),
            "created_ms": int(time.time() * 1000),
            "auto_sync": auto_sync,
            "aligned_anchor_ms": sess.anchor_ms if auto_sync else None,
        }
        sess.takes.append(take)
        sess.status = "recorded"
        return take
