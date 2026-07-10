"""
SessionContextAdapter — compatibility layer between ProjectSession and legacy pipeline.

Legacy modules keep their APIs; this adapter supplies session-scoped paths and logging
without changing translation/TTS/timing algorithms.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterator

from engines.dubbing_engine.project_session import ProjectSession
from engines.dubbing_engine.session_logging import SessionLoggerAdapter

_ACTIVE_ARTIFACTS: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "tubedub_active_artifacts", default=None
)

_DEFAULT_OUTPUT = Path(os.getenv("OUTPUT_DIR", "output"))


class SessionContextAdapter:
    """Bridge ProjectSession → legacy auto-dub / studio expectations."""

    def __init__(self, session: ProjectSession, *, module: str = "auto_dub") -> None:
        self.session = session
        self.module = module
        self.logger = SessionLoggerAdapter(
            logging.getLogger(f"tubedub.{module}"),
            {"session_id": session.session_id, "module": module},
        )

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def task_id(self) -> str:
        return self.session.task_id

    @property
    def base_id(self) -> str:
        return self.session.session_id[:8]

    @property
    def artifacts_dir(self) -> Path:
        return self.session.session_dir

    def path(self, filename: str) -> Path:
        return self.session.session_path(filename)

    def bind_task_info(self, info: dict[str, Any]) -> None:
        info["session_id"] = self.session.session_id
        info["session_dir"] = str(self.session.session_dir)
        info["mux_base_id"] = self.base_id

    def store_pipeline_state(
        self,
        *,
        segments: list[Any] | None = None,
        source_segments: list[Any] | None = None,
        timing_map: list[Any] | None = None,
        translations: list[Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if segments is not None:
            self.session.set_segments(segments)
        if source_segments is not None:
            self.session.set("source_segments", source_segments)
        if timing_map is not None:
            self.session.set_timing_map(timing_map)
        if translations is not None:
            self.session.set_translations(translations)
        if config is not None:
            self.session.set_launch_config(**config)


@contextlib.contextmanager
def activate_session_context(session: ProjectSession | None) -> Iterator[SessionContextAdapter | None]:
    """Set thread-local artifacts directory for the duration of a dubbing run."""
    if session is None:
        yield None
        return
    token = _ACTIVE_ARTIFACTS.set(session.session_dir)
    adapter = SessionContextAdapter(session)
    try:
        yield adapter
    finally:
        _ACTIVE_ARTIFACTS.reset(token)


def get_active_artifacts_dir(
    default: Path | None = None,
    *,
    task_info: dict[str, Any] | None = None,
) -> Path:
    """Resolve artifact directory: active context → task info → default."""
    active = _ACTIVE_ARTIFACTS.get()
    if active is not None:
        return active
    if task_info:
        raw = task_info.get("session_dir")
        if raw:
            p = Path(str(raw))
            if p.is_dir():
                return p
    return default or _DEFAULT_OUTPUT


_SEG_IDX_PATTERNS = (
    re.compile(r"_g(\d+)\.", re.I),
    re.compile(r"_seg(\d+)\.", re.I),
)


def _segment_index_from_filename(name: str) -> int | None:
    for pat in _SEG_IDX_PATTERNS:
        match = pat.search(name)
        if match:
            return int(match.group(1))
    return None


def _glob_segment_audio(out_dir: Path, segment_index: int | None) -> Path | None:
    """Find regenerated TTS in flat output/ by segment index."""
    if segment_index is None or not out_dir.is_dir():
        return None
    patterns = (
        f"*_seg{segment_index:04d}.mp3",
        f"*_seg{segment_index}.mp3",
        f"*_g{segment_index:04d}.mp3",
        f"*_g{segment_index}.mp3",
    )
    hits: list[Path] = []
    for pattern in patterns:
        hits.extend(out_dir.glob(pattern))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def resolve_session_audio(
    filename: str | None,
    *,
    task_info: dict[str, Any] | None = None,
    default_dir: Path | None = None,
    segment_index: int | None = None,
) -> Path:
    """Locate a segment audio file (session dir first, then legacy output/)."""
    if not filename:
        return Path()
    name = Path(str(filename)).name
    out_dir = default_dir or _DEFAULT_OUTPUT
    session_base = get_active_artifacts_dir(out_dir, task_info=task_info)

    search_dirs: list[Path] = []
    for directory in (session_base, out_dir):
        if directory not in search_dirs:
            search_dirs.append(directory)

    for directory in search_dirs:
        candidate = directory / name
        if candidate.is_file():
            return candidate

    if task_info:
        raw_session = task_info.get("session_dir")
        if raw_session:
            retry_root = Path(str(raw_session)) / "post_tts_retry"
            if retry_root.is_dir():
                for hit in retry_root.rglob(name):
                    if hit.is_file():
                        return hit

    seg_idx = segment_index
    if seg_idx is None:
        seg_idx = _segment_index_from_filename(name)
    if seg_idx is None and task_info:
        for seg in task_info.get("segments_data") or []:
            seg_file = seg.get("file") or seg.get("fitted_file")
            if seg_file and Path(str(seg_file)).name == name:
                seg_idx = int(seg.get("index", -1))
                break

    if seg_idx is not None and task_info:
        for seg in task_info.get("segments_data") or []:
            if int(seg.get("index", -1)) != seg_idx:
                continue
            for key in ("file", "fitted_file"):
                alt_name = seg.get(key)
                if not alt_name:
                    continue
                alt_base = Path(str(alt_name)).name
                for directory in search_dirs:
                    alt_path = directory / alt_base
                    if alt_path.is_file():
                        return alt_path

    alt = _glob_segment_audio(out_dir, seg_idx)
    if alt is not None:
        return alt

    return out_dir / name
