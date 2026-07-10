"""Удаление временных артефактов пайплайна (output/, sessions/)."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("tubedub.pipeline_cleanup")

TEMP_GLOBS = (
    "segment_*",
    "chunk_*",
    "temp_*",
    "cache_*",
    "*_seg*.mp3",
    "*_g*.mp3",
    "*_extracted.mp3",
    "*_extracted.wav",
    "*_timed.mp3",
    "*_timed.wav",
    "timing_fit_*",
    "timing_fit_track_*",
    "tubedub_ocr_*",
    "*.tmp.wav",
    "*.tmp.mp3",
)

WORK_SUBDIRS = (
    "slot_fit",
    "post_tts_retry",
    "post_tts_qa",
    "timing_fit",
    "ffmpeg",
    "work",
)


def cleanup_pipeline_artifacts(
    output_dir: Path,
    *,
    keep_names: set[str] | None = None,
    also_temp: bool = True,
) -> int:
    """
    Удаляет временные файлы после успешной сборки.
    keep_names — имена файлов в output_dir, которые нужно сохранить (напр. финальный MP4).
    """
    keep = {Path(n).name for n in (keep_names or set())}
    removed = 0

    if output_dir.is_dir():
        for pattern in TEMP_GLOBS:
            for path in output_dir.glob(pattern):
                if not path.is_file():
                    continue
                if path.name in keep:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError as e:
                    logger.debug("cleanup skip %s: %s", path, e)

    if also_temp:
        tmp_root = Path(tempfile.gettempdir())
        cutoff = time.time() - 300
        for pattern in ("timing_fit_*", "timing_fit_track_*", "tubedub_ocr_*"):
            for path in tmp_root.glob(pattern):
                if not path.is_dir():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                        removed += 1
                except OSError:
                    pass

    if removed:
        logger.info("pipeline_cleanup: removed %d artifacts from %s", removed, output_dir)
    return removed


def cleanup_intermediate_work_dirs(
    base_dir: Path,
    *,
    keep_segment_audio: bool = True,
) -> int:
    """
    Remove pipeline work folders (slot_fit, post_tts_retry, …) and stray temp media.
    Keeps final segment mp3 in session root when keep_segment_audio=True (for Studio mix).
    """
    removed = 0
    base = Path(base_dir)
    if not base.is_dir():
        return 0

    for sub in WORK_SUBDIRS:
        path = base / sub
        if path.is_dir():
            try:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            except OSError as exc:
                logger.debug("cleanup work dir skip %s: %s", path, exc)

    for pattern in ("*.tmp.wav", "*.tmp.mp3", "ffmpeg_*", "*.ffmpeg.*"):
        for path in base.rglob(pattern):
            if not path.is_file():
                continue
            if keep_segment_audio and path.suffix.lower() == ".mp3" and path.parent == base:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass

    for pattern in ("*.json",):
        for path in base.rglob(pattern):
            if not path.is_file():
                continue
            if path.name in ("project.json", "session.json", "manifest.json"):
                continue
            if "dev" in path.parts or "diagnostics" in path.parts:
                continue
            # Never remove segment audio or non-json media via the json sweep.
            if path.suffix.lower() != ".json":
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass

    if removed:
        logger.info(
            "pipeline_cleanup: removed %d intermediate items under %s",
            removed,
            base,
        )
    return removed


def cleanup_after_dub_complete(
    output_dir: Path,
    session_dir: Path | None,
    *,
    keep_names: set[str] | None = None,
) -> int:
    """After MP4 is ready: drop intermediates, keep final video and user exports."""
    removed = cleanup_pipeline_artifacts(output_dir, keep_names=keep_names)
    if session_dir and Path(session_dir).is_dir():
        removed += cleanup_intermediate_work_dirs(
            Path(session_dir), keep_segment_audio=False
        )
        try:
            shutil.rmtree(session_dir, ignore_errors=True)
            removed += 1
        except OSError:
            pass
    return removed
