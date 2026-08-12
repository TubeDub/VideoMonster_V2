"""Удаление временных артефактов пайплайна (output/, sessions/).

После готового MP4: чистим мусор, но session_dir и сегментное аудио НЕ трогаем.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("tubedub.pipeline_cleanup")

# Never include slot_fit_* / pause_run_* / tts_* here.
TEMP_GLOBS = (
    "segment_*",
    "chunk_*",
    "temp_*",
    "cache_*",
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
    # spec v3 intermediate dirs (safe to remove: outputs are copied elsewhere)
    "_demucs_out",
    "_demucs_out_v3",
    "_spk_parts",
)

_PROTECTED_AUDIO_SUFFIXES = (".wav", ".mp3", ".ogg", ".flac")
_PROTECTED_AUDIO_PREFIXES = (
    "slot_fit_",
    "pause_run_",
    "tts_",
    "tts_regen_",
    "pad_silence_",
    # spec v3 speaker reference clips
    "speaker_",
    # spec v3 4-stem outputs
    "dialogue",
    "music_sfx",
    "vocals",
    "drums",
    "bass",
    "other",
)

# Directories that hold spec v3 lineage artifacts we always keep, even after
# a successful dub (they're small and vital for stage restart / audit).
_SPEC_V3_KEEP_DIR_PREFIXES = (
    "speaker_profiles_",
    "diarization_",
    "openddf_",
)


def _is_protected_segment_audio(path: Path) -> bool:
    """True for slot_fit_/pause_run_/tts_/pad_silence_* or any segment media."""
    name = path.name.lower()
    if any(name.startswith(p) for p in _PROTECTED_AUDIO_PREFIXES):
        return True
    if path.suffix.lower() in _PROTECTED_AUDIO_SUFFIXES:
        return True
    return False


def _salvage_protected_audio(src_dir: Path, dest_dir: Path) -> int:
    """Move protected audio out of a work subdir into session root before rmtree."""
    moved = 0
    if not src_dir.is_dir():
        return 0
    for path in src_dir.rglob("*"):
        if not path.is_file():
            continue
        if not _is_protected_segment_audio(path):
            continue
        target = dest_dir / path.name
        if target.resolve() == path.resolve():
            continue
        if target.exists():
            stem, suf = path.stem, path.suffix
            n = 1
            while target.exists():
                target = dest_dir / f"{stem}_keep{n}{suf}"
                n += 1
        try:
            shutil.move(str(path), str(target))
            moved += 1
        except OSError as exc:
            logger.debug("salvage skip %s: %s", path, exc)
    return moved


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
                if _is_protected_segment_audio(path):
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

    When keep_segment_audio=True (default): never delete slot_fit_*/pause_run_*/tts_*
    or .wav/.mp3/.ogg/.flac — salvage them to session root before removing work dirs.
    """
    removed = 0
    base = Path(base_dir)
    if not base.is_dir():
        return 0

    for sub in WORK_SUBDIRS:
        path = base / sub
        if not path.is_dir():
            continue
        if any(sub.startswith(p) for p in _SPEC_V3_KEEP_DIR_PREFIXES):
            continue
        if keep_segment_audio:
            _salvage_protected_audio(path, base)
        try:
            # Delete only non-protected leftovers, then remove empty tree.
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    if keep_segment_audio and _is_protected_segment_audio(child):
                        continue
                    try:
                        child.unlink()
                        removed += 1
                    except OSError:
                        pass
                elif child.is_dir():
                    try:
                        child.rmdir()
                    except OSError:
                        pass
            try:
                if not any(path.iterdir()):
                    path.rmdir()
                    removed += 1
                elif not keep_segment_audio:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError as exc:
                logger.debug("cleanup work dir skip %s: %s", path, exc)
        except OSError as exc:
            logger.debug("cleanup work dir skip %s: %s", path, exc)

    for pattern in ("*.tmp.wav", "*.tmp.mp3", "ffmpeg_*", "*.ffmpeg.*"):
        for path in base.rglob(pattern):
            if not path.is_file():
                continue
            if keep_segment_audio and _is_protected_segment_audio(path):
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
    """После готового MP4: чистим мусор, но session_dir и сегментное аудио НЕ трогаем."""
    removed = cleanup_pipeline_artifacts(output_dir, keep_names=keep_names)
    if session_dir and Path(session_dir).is_dir():
        removed += cleanup_intermediate_work_dirs(
            Path(session_dir),
            keep_segment_audio=True,  # ОБЯЗАТЕЛЬНО True
        )
        # Session directory itself is never recursively deleted after dub.
    return removed
