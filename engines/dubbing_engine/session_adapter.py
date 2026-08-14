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
        try:
            info["session_dir"] = str(Path(self.session.session_dir).resolve())
        except OSError:
            info["session_dir"] = str(self.session.session_dir)
        info["mux_base_id"] = self.base_id
        try:
            info["task_id"] = self.task_id
        except Exception:
            pass

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
    """Locate a segment audio file (session dir first, then legacy output/).

    Accepts basename OR relative/absolute paths (e.g. output/sessions/.../pause/*.wav).
    """
    if not filename:
        return Path()
    raw = Path(str(filename))
    name = raw.name
    out_dir = Path(default_dir or _DEFAULT_OUTPUT)
    session_base = get_active_artifacts_dir(out_dir, task_info=task_info)
    project_root = out_dir.parent if out_dir.name.lower() == "output" else out_dir

    # 1) Absolute / already-valid path
    if raw.is_file():
        return raw.resolve()

    # 2) Relative path with directories (pause/closed_loop/session layouts)
    path_candidates: list[Path] = []
    if len(raw.parts) > 1:
        path_candidates.extend(
            [
                project_root / raw,
                Path.cwd() / raw,
                out_dir / raw,
            ]
        )
        parts = raw.parts
        if parts and parts[0].lower() == "output":
            # output/sessions/... → <project>/output/sessions/... already covered;
            # also try relative to out_dir by stripping the leading output/
            path_candidates.append(out_dir / Path(*parts[1:]))
            path_candidates.append(project_root / Path(*parts))
        else:
            path_candidates.append(session_base / raw)
    for cand in path_candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue

    # 3) Basename in known roots — Stage 28 §A2 always checks the closed_loop
    # subtree first so pads/regens land where the census (also §A3) looks.
    search_dirs: list[Path] = []
    task_id_val = ""
    if task_info:
        raw_tid = task_info.get("task_id")
        if raw_tid:
            task_id_val = str(raw_tid).strip()
    for directory in (session_base, out_dir):
        if directory not in search_dirs:
            search_dirs.append(directory)

    priority_dirs: list[Path] = []
    if task_id_val:
        priority_dirs.append(session_base / "closed_loop" / task_id_val)
    priority_dirs.append(session_base / "closed_loop")
    for directory in priority_dirs + search_dirs:
        candidate = directory / name
        if candidate.is_file():
            try:
                return candidate.resolve()
            except OSError:
                return candidate

    # 4) Recursive search under session (pause/, closed_loop/, post_tts_retry/, …)
    search_roots: list[Path] = []
    if task_info:
        raw_session = task_info.get("session_dir")
        if raw_session:
            search_roots.append(Path(str(raw_session)))
        arts = task_info.get("artifacts_dir")
        if arts:
            search_roots.append(Path(str(arts)))
    search_roots.extend([session_base, out_dir / "sessions", out_dir])
    seen_roots: set[str] = set()
    for root in search_roots:
        key = str(root)
        if key in seen_roots or not root.exists():
            continue
        seen_roots.add(key)
        try:
            for hit in root.rglob(name):
                if hit.is_file() and hit.stat().st_size > 0:
                    return hit.resolve()
        except OSError:
            continue

    # 5) Segment row may still hold a full relative path — try it directly
    if task_info:
        for seg in task_info.get("segments_data") or []:
            if not isinstance(seg, dict):
                continue
            for key in ("file", "fitted_file", "tts_file_path", "runtime_registry_path"):
                alt = seg.get(key)
                if not alt:
                    continue
                if Path(str(alt)).name != name:
                    continue
                alt_path = Path(str(alt))
                if alt_path.is_file():
                    return alt_path.resolve()
                for base in (project_root, Path.cwd(), out_dir):
                    cand = base / alt_path
                    if cand.is_file():
                        return cand.resolve()
                    parts = alt_path.parts
                    if parts and parts[0].lower() == "output":
                        cand2 = out_dir / Path(*parts[1:])
                        if cand2.is_file():
                            return cand2.resolve()

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
            for key in ("file", "fitted_file", "tts_file_path"):
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
