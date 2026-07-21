"""Cleanup Engine — remove pipeline temp junk; never touch user finals."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.cleanup_engine")

# Explicit allowlist of deletable name patterns (relative to session/output)
TEMP_FILE_GLOBS = (
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
    "*.tmp.wav",
    "*.tmp.mp3",
    "*.tmp.json",
    "*_whisper*",
    "tqe_tmp_*",
    "retry_tmp_*",
    "meaning_tmp_*",
    "translation_tmp_*",
)

TEMP_SUBDIRS = (
    "temp",
    "work",
    "ffmpeg",
    "slot_fit",
    "post_tts_retry",
    "post_tts_qa",
    "timing_fit",
    "tqe_work",
    "retry_work",
    "session_cache",
)

# NEVER delete these (basename match or suffix)
PROTECTED_SUFFIXES = (".mp4", ".mkv", ".mov")
PROTECTED_NAMES = {
    "settings.json",
    "config.json",
    "voice_catalog.json",
    "requirements.txt",
    "requirements_desktop.txt",
}


@dataclass
class CleanupReport:
    files_deleted: int = 0
    bytes_freed: int = 0
    dirs_removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_deleted": self.files_deleted,
            "bytes_freed": self.bytes_freed,
            "mb_freed": round(self.bytes_freed / 1024**2, 2),
            "dirs_removed": list(self.dirs_removed),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
            "scope": self.scope,
        }


def _is_protected(path: Path, keep_names: set[str]) -> bool:
    name = path.name
    if name in keep_names or name in PROTECTED_NAMES:
        return True
    if path.suffix.lower() in PROTECTED_SUFFIXES and name in keep_names:
        return True
    # Final dub outputs often named like <id>_dub.mp4 — only protected if in keep_names
    if path.suffix.lower() in PROTECTED_SUFFIXES and name not in keep_names:
        # Still protect any file under projects/ or models/
        parts = {p.lower() for p in path.parts}
        if "projects" in parts or "models" in parts or "presets" in parts:
            return True
    parts = {p.lower() for p in path.parts}
    if "projects" in parts or "models" in parts or "user_settings" in parts:
        return True
    return False


def _unlink(path: Path, report: CleanupReport, keep_names: set[str]) -> None:
    if _is_protected(path, keep_names):
        report.skipped.append(str(path))
        return
    try:
        sz = path.stat().st_size if path.is_file() else 0
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            report.dirs_removed.append(str(path))
        else:
            path.unlink(missing_ok=True)
        report.files_deleted += 1
        report.bytes_freed += sz
    except OSError as exc:
        report.errors.append(f"{path}: {exc}")


class CleanupEngine:
    """Centralized post-run cleanup with user-asset protection."""

    def __init__(self, app_dir: Path | str):
        self.app_dir = Path(app_dir)
        self.output_dir = self.app_dir / "output"

    def cleanup_after_success(
        self,
        *,
        session_dir: Path | str | None = None,
        keep_names: set[str] | None = None,
        remove_session_temp: bool = True,
        log_retention_days: int = 14,
    ) -> CleanupReport:
        keep = {Path(n).name for n in (keep_names or set())}
        report = CleanupReport(scope="after_success")

        # Delegate existing safe cleanup first
        try:
            from engines.storage_cleanup import cleanup_after_dub_with_report

            legacy = cleanup_after_dub_with_report(
                self.app_dir,
                self.output_dir,
                Path(session_dir) if session_dir else None,
                keep_names=keep,
            )
            report.files_deleted += int(legacy.files_deleted or 0)
            report.bytes_freed += int(legacy.bytes_freed or 0)
            report.dirs_removed.extend(list(legacy.directories_cleaned or []))
            report.errors.extend(list(legacy.errors or []))
        except Exception as exc:
            report.errors.append(f"legacy_cleanup:{exc}")

        # Extra temp globs in output/
        if self.output_dir.is_dir():
            for pattern in TEMP_FILE_GLOBS:
                for path in self.output_dir.glob(pattern):
                    if path.is_file():
                        _unlink(path, report, keep)

        # Session isolation: wipe session/temp (+ known work subdirs)
        if session_dir and remove_session_temp:
            sdir = Path(session_dir)
            if sdir.is_dir():
                for sub in TEMP_SUBDIRS:
                    target = sdir / sub
                    if target.exists():
                        _unlink(target, report, keep)
                # Remove empty session only if fully temp — never delete finals in session root
                for pattern in TEMP_FILE_GLOBS:
                    for path in sdir.glob(pattern):
                        if path.is_file():
                            _unlink(path, report, keep)

        # Old logs policy
        logs_dir = self.output_dir / "logs"
        if logs_dir.is_dir() and log_retention_days > 0:
            cutoff = time.time() - log_retention_days * 86400
            for path in logs_dir.glob("*.log"):
                try:
                    if path.stat().st_mtime < cutoff:
                        _unlink(path, report, keep)
                except OSError:
                    pass

        # Transient TQE work (never delete quality/failures datasets)
        tqe_tmp = self.output_dir / "tqe_work"
        if tqe_tmp.exists():
            _unlink(tqe_tmp, report, keep)

        logger.info(
            "[CleanupEngine] deleted=%d freed_mb=%.2f skipped=%d errors=%d",
            report.files_deleted,
            report.bytes_freed / 1024**2,
            len(report.skipped),
            len(report.errors),
        )
        self._write_cleanup_log(report, session_dir=session_dir)
        return report

    def _write_cleanup_log(
        self,
        report: CleanupReport,
        *,
        session_dir: Path | str | None = None,
    ) -> None:
        """TRH BUG10 — cleanup.log with files/MB/reason/time."""
        try:
            targets: list[Path] = []
            if session_dir:
                targets.append(Path(session_dir) / "cleanup.log")
            targets.append(self.output_dir / "logs" / "cleanup.log")
            line = (
                f"{time.strftime('%Y-%m-%dT%H:%M:%S')} "
                f"deleted={report.files_deleted} "
                f"mb={round(report.bytes_freed / 1024**2, 2)} "
                f"reason=after_success "
                f"dirs={len(report.dirs_removed)} "
                f"errors={len(report.errors)}\n"
            )
            for path in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception as exc:
            logger.debug("cleanup.log write failed: %s", exc)


def cleanup_project_success(
    app_dir: Path | str,
    *,
    session_dir: Path | str | None = None,
    keep_names: set[str] | None = None,
) -> dict[str, Any]:
    return CleanupEngine(app_dir).cleanup_after_success(
        session_dir=session_dir,
        keep_names=keep_names,
    ).to_dict()
