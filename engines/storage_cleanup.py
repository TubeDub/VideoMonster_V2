"""Safe temp-only storage cleanup with full audit trail (TZ Storage §9/§10).

DELETABLE (explicit allowlist):
  • pipeline work dirs (slot_fit, post_tts_retry, …)
  • orphaned / finished session dirs (never active studio/running tasks)
  • loose temp media in output/
  • OS temp timing_fit_* dirs
  • pipeline cache (output/cache/pipeline)
  • LLM rewrite disk cache (data/cache/llm_rewrite_cache.json)
  • HF incomplete download fragments (.tmp, .incomplete)

NEVER DELETED:
  • projects/, user settings, models/, final MP4, presets, logs, uploads
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.storage_cleanup")

# Session dirs younger than this with an active task are skipped.
_ORPHAN_SESSION_MIN_AGE_S = 3600  # 1 hour safety margin


@dataclass
class StorageCleanupReport:
    """OpenDDF Storage Report payload (TZ §10)."""

    files_scanned: int = 0
    files_deleted: int = 0
    bytes_freed: int = 0
    directories_cleaned: list[str] = field(default_factory=list)
    directories_skipped: list[dict[str, str]] = field(default_factory=list)
    scope: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_created": self.files_scanned,
            "files_deleted": self.files_deleted,
            "bytes_freed": self.bytes_freed,
            "mb_freed": round(self.bytes_freed / 1024**2, 2),
            "directories_cleaned": list(self.directories_cleaned),
            "directories_skipped": list(self.directories_skipped),
            "scope": self.scope,
            "errors": list(self.errors),
        }

    def merge(self, other: "StorageCleanupReport") -> None:
        self.files_scanned += other.files_scanned
        self.files_deleted += other.files_deleted
        self.bytes_freed += other.bytes_freed
        self.directories_cleaned.extend(other.directories_cleaned)
        self.directories_skipped.extend(other.directories_skipped)
        self.errors.extend(other.errors)


def _dir_bytes(path: Path) -> int:
    try:
        from engines.model_manager.storage import dir_size

        return int(dir_size(path))
    except Exception:
        total = 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        if not path.exists():
            return 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total


def _remove_file(path: Path, report: StorageCleanupReport) -> None:
    report.files_scanned += 1
    try:
        sz = path.stat().st_size if path.is_file() else 0
        path.unlink()
        report.files_deleted += 1
        report.bytes_freed += sz
    except OSError as exc:
        report.errors.append(f"{path}: {exc}")


def _remove_tree(path: Path, report: StorageCleanupReport, *, label: str | None = None) -> None:
    if not path.exists():
        return
    report.files_scanned += 1
    sz = _dir_bytes(path)
    try:
        if path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=True)
        report.files_deleted += 1
        report.bytes_freed += sz
        report.directories_cleaned.append(label or str(path))
    except OSError as exc:
        report.errors.append(f"{path}: {exc}")


def _skip(report: StorageCleanupReport, path: Path, reason: str) -> None:
    report.directories_skipped.append({"path": str(path), "reason": reason})


def _active_task_session_ids(app_dir: Path) -> set[str]:
    """Session / task ids that must not be deleted."""
    active: set[str] = set()
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            for tid, task in AUTO_TASKS.items():
                active.add(str(tid))
                info = task.get("info") or {}
                sd = info.get("session_dir") or ""
                if sd:
                    active.add(Path(str(sd)).name)
                if task.get("status") in ("running", "studio_ready", "preparing"):
                    active.add(str(tid))
    except Exception:
        pass
    return active


def cleanup_pipeline_temp(
    app_dir: Path,
    *,
    include_sessions: bool = True,
    include_slot_fit: bool = True,
    include_output_globs: bool = True,
    active_session_ids: set[str] | None = None,
) -> StorageCleanupReport:
    """Remove dubbing temp artifacts only — never user projects or final MP4."""
    from engines.pipeline_cleanup import TEMP_GLOBS, WORK_SUBDIRS, cleanup_pipeline_artifacts

    app_dir = Path(app_dir)
    output = app_dir / "output"
    report = StorageCleanupReport(scope="pipeline_temp")
    active = active_session_ids if active_session_ids is not None else _active_task_session_ids(app_dir)
    now = time.time()

    if include_output_globs and output.is_dir():
        keep_mp4 = {p.name for p in output.glob("*_OUTPUT_*.mp4")}
        for pattern in TEMP_GLOBS:
            for path in output.glob(pattern):
                if not path.is_file():
                    continue
                if path.name in keep_mp4:
                    _skip(report, path, "final_output_mp4")
                    continue
                _remove_file(path, report)

    if include_slot_fit:
        slot_root = output / "slot_fit"
        if slot_root.is_dir():
            _remove_tree(slot_root, report, label=str(slot_root))

    sessions_root = output / "sessions"
    if include_sessions and sessions_root.is_dir():
        for sess in sessions_root.iterdir():
            if not sess.is_dir():
                continue
            sid = sess.name
            if sid in active:
                _skip(report, sess, "active_task_session")
                continue
            try:
                age = now - sess.stat().st_mtime
            except OSError:
                age = 0
            if age < _ORPHAN_SESSION_MIN_AGE_S:
                _skip(report, sess, "session_too_recent")
                continue
            # Drop work subdirs first, then whole session.
            for sub in WORK_SUBDIRS:
                subpath = sess / sub
                if subpath.is_dir():
                    _remove_tree(subpath, report, label=str(subpath))
            _remove_tree(sess, report, label=str(sess))

    # OS-level stale timing_fit temps
    tmp_root = Path(tempfile.gettempdir())
    cutoff = now - 300
    for pattern in ("timing_fit_*", "timing_fit_track_*", "tubedub_ocr_*", "vm_timed_*.wav"):
        for path in tmp_root.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime >= cutoff:
                    _skip(report, path, "recent_os_temp")
                    continue
                if path.is_dir() and path.stat().st_mtime >= cutoff:
                    _skip(report, path, "recent_os_temp")
                    continue
            except OSError:
                pass
            _remove_tree(path, report, label=str(path))

    # Delegate flat output cleanup (respects keep_names empty — temp only)
    try:
        n = cleanup_pipeline_artifacts(output, keep_names=set(), also_temp=False)
        if n:
            report.files_deleted += n
            report.directories_cleaned.append(f"output_artifacts:{n}")
    except Exception as exc:
        report.errors.append(f"cleanup_pipeline_artifacts: {exc}")

    if report.files_deleted or report.bytes_freed:
        logger.info(
            "storage_cleanup pipeline_temp: deleted=%d freed=%.1fMB",
            report.files_deleted,
            report.bytes_freed / 1024**2,
        )
    return report


def cleanup_pipeline_cache(app_dir: Path) -> StorageCleanupReport:
    """Pipeline STT/translation cache — safe to clear, rebuilds on next run."""
    report = StorageCleanupReport(scope="pipeline_cache")
    cache = Path(app_dir) / "output" / "cache" / "pipeline"
    if cache.is_dir():
        _remove_tree(cache, report, label=str(cache))
    hf_tmp = Path(app_dir) / "cache" / "huggingface" / "tmp"
    if hf_tmp.is_dir():
        for p in hf_tmp.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".tmp", ".incomplete", ".download"):
                _remove_file(p, report)
    try:
        from engines.model_manager.integrity import cleanup_temp_files

        cleanup_temp_files(Path(app_dir))
        report.directories_cleaned.append("hf_incomplete_fragments")
    except Exception as exc:
        report.errors.append(f"hf cleanup: {exc}")
    return report


def cleanup_llm_rewrite_cache(app_dir: Path) -> StorageCleanupReport:
    """TubeDub LLM rewrite JSON cache only — not external Ollama models."""
    report = StorageCleanupReport(scope="llm_rewrite_cache")
    cache_file = Path(app_dir) / "data" / "cache" / "llm_rewrite_cache.json"
    if cache_file.is_file():
        _remove_file(cache_file, report)
        report.directories_cleaned.append(str(cache_file))
    try:
        import engines.llm_cache as lc

        lc._MEM.clear()
        lc._DISK_LOADED = False
    except Exception:
        pass
    return report


def cleanup_all_temp_and_cache(app_dir: Path) -> StorageCleanupReport:
    """Single-button cleanup: temp files + pipeline cache + LLM rewrite cache."""
    combined = StorageCleanupReport(scope="all_temp_and_cache")
    for part in (
        cleanup_pipeline_temp(app_dir),
        cleanup_pipeline_cache(app_dir),
        cleanup_llm_rewrite_cache(app_dir),
    ):
        combined.merge(part)
    _persist_last_cleanup(app_dir, combined)
    return combined


def cleanup_after_dub_with_report(
    app_dir: Path,
    output_dir: Path,
    session_dir: Path | None,
    *,
    keep_names: set[str] | None = None,
) -> StorageCleanupReport:
    """Post-dub cleanup with OpenDDF-compatible report (wraps pipeline_cleanup)."""
    from engines.pipeline_cleanup import cleanup_after_dub_complete

    report = StorageCleanupReport(scope="after_dub_complete")
    before_bytes = 0
    if session_dir and Path(session_dir).is_dir():
        before_bytes = _dir_bytes(Path(session_dir))
    try:
        removed = cleanup_after_dub_complete(output_dir, session_dir, keep_names=keep_names)
        report.files_deleted = max(removed, 1) if removed else 0
        report.bytes_freed = before_bytes
        if session_dir:
            report.directories_cleaned.append(str(session_dir))
        report.directories_cleaned.append(str(output_dir))
    except Exception as exc:
        report.errors.append(str(exc))
    _persist_last_cleanup(app_dir, report)
    return report


def _persist_last_cleanup(app_dir: Path, report: StorageCleanupReport) -> None:
    """Append cleanup event for OpenDDF / diagnostics."""
    try:
        log_path = Path(app_dir) / "output" / "dev" / "storage_cleanup_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {**report.to_dict(), "ts": time.time()}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def startup_storage_audit(app_dir: Path) -> StorageCleanupReport:
    """On app start: remove stale temp junk that accumulated between sessions."""
    report = cleanup_pipeline_temp(
        app_dir,
        include_sessions=True,
        include_slot_fit=True,
        include_output_globs=True,
    )
    if report.files_deleted:
        logger.info(
            "startup storage audit: freed %.1f MB (%d items)",
            report.bytes_freed / 1024**2,
            report.files_deleted,
        )
    return report
