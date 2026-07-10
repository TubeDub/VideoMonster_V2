"""
ProjectSession — per-project isolation boundary.

Every dubbing run gets its own ProjectSession.
All temporary files, caches, and state are scoped
to a unique session_id derived from task_id.

Fixes the critical isolation bug where data from a previous project
(text, segments, audio) leaks into the next project.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from engines.dubbing_engine.session_logging import SessionLoggerAdapter

logger = logging.getLogger("tubedub.project_session")

# Global registry: session_id → ProjectSession
# Access only through SESSION_LOCK
_SESSIONS: dict[str, "ProjectSession"] = {}
_SESSION_LOCK = RLock()

# How long to keep finished sessions in memory before expiring them (seconds)
_SESSION_TTL_SEC: int = 3600  # 1 hour


class ProjectSession:
    """
    Encapsulates all state for a single dubbing project.

    Never share state between sessions. Each session has:
      • session_id  — unique identifier
      • session_dir — isolated temp directory in output/sessions/<session_id>/
      • data        — arbitrary session-scoped storage (replaces globals)
    """

    def __init__(
        self,
        session_id: str,
        output_dir: Path,
        task_id: str = "",
        content_mode: str = "movie",
    ) -> None:
        self.session_id = session_id
        self.task_id = task_id or session_id
        self.content_mode = content_mode
        self.created_at = time.time()
        self.finished_at: float = 0.0
        self._data: dict[str, Any] = {}
        self._tracked_files: list[Path] = []
        self._launch_config: dict[str, Any] = {}

        # Isolated temp directory for this session
        self.session_dir = output_dir / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.session_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.log = SessionLoggerAdapter(
            logging.getLogger("tubedub.project_session"),
            {"session_id": session_id, "module": "ProjectSession"},
        )
        self.log.info(
            "created task_id=%s mode=%s dir=%s",
            task_id, content_mode, self.session_dir,
        )

    def set_launch_config(self, **kwargs: Any) -> None:
        self._launch_config.update(kwargs)
        self._data["launch_config"] = dict(self._launch_config)

    def get_launch_config(self) -> dict[str, Any]:
        return dict(self._launch_config)

    def set_segments(self, segments: list[Any]) -> None:
        self.set("segments", segments)

    def set_translations(self, translations: list[Any]) -> None:
        self.set("translations", translations)

    def set_timing_map(self, timing_map: list[Any]) -> None:
        self.set("timing_map", timing_map)

    def set_result_ref(self, key: str, path: str | Path) -> None:
        results = self.get("results") or {}
        results[key] = str(path)
        self.set("results", results)
        self.track_file(Path(path))

    def store_pipeline_state(
        self,
        *,
        segments: list[Any] | None = None,
        source_segments: list[Any] | None = None,
        timing_map: list[Any] | None = None,
        translations: list[Any] | None = None,
    ) -> None:
        if segments is not None:
            self.set_segments(segments)
        if source_segments is not None:
            self.set("source_segments", source_segments)
        if timing_map is not None:
            self.set_timing_map(timing_map)
        if translations is not None:
            self.set_translations(translations)

    # ── Data access ───────────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, mapping: dict[str, Any]) -> None:
        self._data.update(mapping)

    # ── File tracking ─────────────────────────────────────────────────────────

    def track_file(self, path: Path) -> Path:
        """Register a temp file. All tracked files are deleted on cleanup()."""
        self._tracked_files.append(path)
        return path

    def session_path(self, filename: str) -> Path:
        """Return a path inside this session's isolated directory."""
        p = self.session_dir / filename
        self._tracked_files.append(p)
        return p

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def finish(self) -> None:
        self.finished_at = time.time()
        self.log.info("finished")

    def cleanup(self, *, keep_output: bool = True) -> int:
        """
        Delete all session-scoped temp files.
        If keep_output=False, also removes the session directory.
        Returns number of files removed.
        """
        removed = 0
        for path in self._tracked_files:
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.debug("[Session] cleanup skip %s: %s", path, exc)
        self._tracked_files.clear()

        if not keep_output and self.session_dir.exists():
            try:
                shutil.rmtree(self.session_dir, ignore_errors=True)
                self.log.info("removed session_dir (%d tracked files)", removed)
            except OSError as exc:
                self.log.debug("rmtree failed: %s", exc)

        return removed

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "content_mode": self.content_mode,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "tracked_files": len(self._tracked_files),
            "data_keys": list(self._data.keys()),
        }


# ── Registry helpers ───────────────────────────────────────────────────────────

def create_session(
    task_id: str,
    output_dir: Path,
    content_mode: str = "movie",
) -> ProjectSession:
    """Create and register a new ProjectSession for task_id."""
    session_id = task_id or uuid.uuid4().hex
    session = ProjectSession(
        session_id=session_id,
        output_dir=output_dir,
        task_id=task_id,
        content_mode=content_mode,
    )
    with _SESSION_LOCK:
        # Evict expired sessions first (prevents unbounded memory growth)
        _evict_expired()
        _SESSIONS[session_id] = session
    return session


def get_session(task_id: str) -> ProjectSession | None:
    with _SESSION_LOCK:
        return _SESSIONS.get(task_id)


def finish_session(task_id: str) -> None:
    with _SESSION_LOCK:
        session = _SESSIONS.get(task_id)
    if session:
        session.finish()


def cleanup_session(task_id: str, *, keep_output: bool = True) -> int:
    """Clean up files for a session and remove it from registry."""
    with _SESSION_LOCK:
        session = _SESSIONS.pop(task_id, None)
    if session:
        return session.cleanup(keep_output=keep_output)
    return 0


def _evict_expired() -> None:
    """Remove old finished sessions to prevent memory leak. Call under SESSION_LOCK."""
    cutoff = time.time() - _SESSION_TTL_SEC
    stale = [
        sid for sid, s in _SESSIONS.items()
        if s.finished_at > 0 and s.finished_at < cutoff
    ]
    for sid in stale:
        session = _SESSIONS.pop(sid)
        try:
            session.cleanup(keep_output=True)
        except Exception as exc:
            logger.debug("[Session] evict cleanup %s: %s", sid, exc)
    if stale:
        logger.info("[Session] evicted %d expired sessions", len(stale))


def active_sessions() -> list[dict[str, Any]]:
    with _SESSION_LOCK:
        return [s.summary() for s in _SESSIONS.values()]
