"""Cleanup Manager — single delete gate (TZ §27–31).

Ownership classifier (NOT by .wav/.mp3 extension):
  FINAL  — user exports / final.mp4 — never auto-deleted
  STUDIO — kept only if Keep Studio Assets
  TEMP   — deleted after pipeline and in ``finally`` on failure
  DEBUG  — TTL
  CACHE  — key/owner/TTL, never mixed with TEMP
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.pipeline_state import PipelineState, get_pipeline_state
from engines.pipeline_integrity.runtime_registry import RuntimeRegistry, get_or_create_registry
from engines.pipeline_integrity.tts_artifact_lifecycle import (
    TTSLifecycleState,
    can_cleanup_wav,
    get_tts_lifecycle,
)
from engines.pipeline_integrity.wav_ownership import (
    CleanupViolationError,
    assert_cleanup_allowed,
)

logger = logging.getLogger("tubedub.cleanup_manager")

DEBUG_TTL_SEC = 7 * 24 * 3600
CACHE_TTL_SEC = 14 * 24 * 3600

PROTECTED_MUX_PREFIXES: tuple[str, ...] = (
    "slot_fit_",
    "pause_run_",
    "tts_",
    "tts_regen_",
    "pad_silence_",
    "softpad_",
)

FINAL_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm"}
_TEMP_NAME_PREFIXES = (
    "temp_",
    "tmp_",
    "chunk_",
    "timing_fit_",
    "extracted_",
    "work_",
)
_TEMP_DIR_NAMES = {
    "temp",
    "tmp",
    "work",
    "ffmpeg",
    "slot_fit",
    "post_tts_retry",
    "post_tts_qa",
    "timing_fit",
    "tqe_work",
    "retry_work",
    "session_cache",
}


class ArtifactCategory(str, Enum):
    FINAL = "FINAL"
    STUDIO = "STUDIO"
    TEMP = "TEMP"
    DEBUG = "DEBUG"
    CACHE = "CACHE"


@dataclass
class CleanupReport:
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "removed": list(self.removed),
            "skipped": list(self.skipped),
            "blocked": list(self.blocked),
            "preserved": list(self.preserved),
            "cleanup_deleted_files": list(self.removed),
            "cleanup_preserved_files": list(self.preserved) + list(self.blocked),
        }


def is_protected_mux_prefix(path: Path | str) -> bool:
    name = Path(path).name.lower()
    return any(name.startswith(p) for p in PROTECTED_MUX_PREFIXES)


def classify_artifact(
    path: Path | str,
    *,
    info: dict[str, Any] | None = None,
    keep_studio: bool = False,
) -> ArtifactCategory:
    """Classify by ownership / role — never by audio extension alone."""
    p = Path(path)
    name = p.name.lower()
    parts = {x.lower() for x in p.parts}
    info = info or {}

    keep_names = {str(n).lower() for n in (info.get("cleanup_keep_names") or []) if n}
    out_name = Path(str(info.get("output_file") or info.get("output_path_full") or "")).name.lower()
    if out_name:
        keep_names.add(out_name)

    if name in keep_names:
        return ArtifactCategory.FINAL
    if p.suffix.lower() in FINAL_SUFFIXES:
        return ArtifactCategory.FINAL
    if name in {"final.mp4", "final.mkv"} or name.endswith("_output.mp4"):
        return ArtifactCategory.FINAL
    if "_output_" in name and p.suffix.lower() in FINAL_SUFFIXES:
        return ArtifactCategory.FINAL

    if "cache" in parts or name.startswith("cache_") or "model_cache" in parts:
        return ArtifactCategory.CACHE

    if (
        "diag_" in name
        or name.startswith("openddf")
        or "debug" in parts
        or name.endswith("_debug.json")
        or name.startswith("engine_")
    ):
        return ArtifactCategory.DEBUG

    if keep_studio and (
        "studio" in parts
        or name.endswith("_studio.json")
        or "studio_sessions" in parts
    ):
        return ArtifactCategory.STUDIO

    return ArtifactCategory.TEMP


class CleanupManager:
    """Only pathway allowed to delete pipeline artifacts."""

    def __init__(self, info: dict[str, Any], registry: RuntimeRegistry | None = None) -> None:
        self.info = info if isinstance(info, dict) else {}
        self.registry = registry or get_or_create_registry(self.info)

    def pipeline_allows_cleanup(self) -> bool:
        state = get_pipeline_state(self.info)
        return state == PipelineState.EXPORTED

    def try_unlink_segment_wav(
        self,
        seg: dict[str, Any],
        path: Path | str,
        *,
        actor: str = "cleanup_manager",
        force: bool = False,
    ) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        if not force:
            if not can_cleanup_wav(seg) and not self.pipeline_allows_cleanup():
                raise CleanupViolationError(
                    f"WAV cleanup forbidden before EXPORTED/RELEASED "
                    f"(state={get_tts_lifecycle(seg).value})",
                    stage="cleanup",
                    details={
                        "segment_uuid": seg.get("segment_uuid"),
                        "path": str(p),
                        "tts_lifecycle": get_tts_lifecycle(seg).value,
                        "pipeline_state": str(get_pipeline_state(self.info).value),
                    },
                )
            assert_cleanup_allowed(seg)
            rec = self.registry.get(str(seg.get("segment_uuid") or ""))
            if rec and rec.ref_count > 1:
                raise CleanupViolationError(
                    f"ref_count={rec.ref_count} — cannot delete",
                    stage="cleanup",
                    details={"segment_uuid": seg.get("segment_uuid")},
                )
        try:
            p.unlink()
            self.registry.mark_deleted(str(seg.get("segment_uuid") or ""), actor=actor)
            logger.info("[CLEANUP] removed %s actor=%s", p, actor)
            return True
        except OSError as exc:
            logger.warning("[CLEANUP] failed %s: %s", p, exc)
            return False

    def cleanup_orphans(
        self,
        directories: list[Path],
        *,
        segments: list[dict[str, Any]] | None = None,
        older_than_sec: float = 3600,
        actor: str = "cleanup_manager",
    ) -> CleanupReport:
        """
        Remove orphan temp audio only when pipeline is EXPORTED or files are
        unregistered AND older than threshold. Registered live WAVs are never touched.
        Mux-input prefixes are never deleted while the pipeline is not EXPORTED.
        """
        report = CleanupReport()
        live_names: set[str] = set()
        for rec in self.registry.records.values():
            if rec.path:
                live_names.add(Path(rec.path).name)
        for seg in segments or self.info.get("segments_data") or []:
            if not isinstance(seg, dict):
                continue
            for key in ("file", "tts_file_path"):
                name = Path(str(seg.get(key) or "")).name
                if name:
                    live_names.add(name)
            if get_tts_lifecycle(seg) not in (
                TTSLifecycleState.EXPORTED,
                TTSLifecycleState.RELEASED,
            ):
                for key in ("file", "tts_file_path", "runtime_registry_path"):
                    name = Path(str(seg.get(key) or "")).name
                    if name:
                        live_names.add(name)

        now = time.time()
        allow_registered = self.pipeline_allows_cleanup()
        mux_live = not allow_registered
        for root in directories:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in {".wav", ".mp3", ".ogg", ".tmp"}:
                    continue
                if mux_live and is_protected_mux_prefix(p):
                    report.blocked.append(str(p))
                    continue
                cat = classify_artifact(p, info=self.info)
                if cat == ArtifactCategory.FINAL:
                    report.preserved.append(str(p))
                    continue
                if p.name in live_names and not allow_registered:
                    report.blocked.append(str(p))
                    continue
                try:
                    age = now - p.stat().st_mtime
                except OSError:
                    continue
                if age < older_than_sec and not allow_registered:
                    report.skipped.append(str(p))
                    continue
                if p.name in live_names and allow_registered:
                    report.blocked.append(str(p))
                    continue
                try:
                    p.unlink()
                    report.removed.append(str(p))
                except OSError:
                    report.skipped.append(str(p))
        return report

    def cleanup_session(
        self,
        session_dir: Path | str | None,
        *,
        success: bool = True,
        keep_studio: bool = False,
        keep_names: set[str] | None = None,
        mux_inputs_live: bool | None = None,
        actor: str = "cleanup_manager",
    ) -> CleanupReport:
        """Delete TEMP (and expired DEBUG/CACHE). Never auto-delete FINAL.

        TEMP is removed after success AND on failure (TZ §29). Protected mux
        prefixes stay while they are still mix inputs.
        """
        report = CleanupReport()
        if self.info.get("cleanup_manager_ran") and actor != "force":
            # Other deleters become no-ops after the unified gate already ran.
            report.skipped.append("cleanup_manager_already_ran")
            return report

        keep = {str(n) for n in (keep_names or set()) if n}
        out_name = Path(str(self.info.get("output_file") or "")).name
        if out_name:
            keep.add(out_name)
        self.info["cleanup_keep_names"] = sorted(keep)

        live_mux = (
            (not self.pipeline_allows_cleanup())
            if mux_inputs_live is None
            else bool(mux_inputs_live)
        )
        keep_studio = bool(keep_studio or self.info.get("keep_studio_assets"))
        now = time.time()
        roots: list[Path] = []
        if session_dir:
            sd = Path(session_dir)
            if sd.exists():
                roots.append(sd)

        def _maybe_delete(p: Path) -> None:
            if not p.is_file():
                return
            cat = classify_artifact(p, info=self.info, keep_studio=keep_studio)
            if p.name in keep or cat == ArtifactCategory.FINAL:
                report.preserved.append(str(p))
                return
            if cat == ArtifactCategory.STUDIO and keep_studio:
                report.preserved.append(str(p))
                return
            if live_mux and is_protected_mux_prefix(p):
                report.blocked.append(str(p))
                return
            try:
                age = now - p.stat().st_mtime
            except OSError:
                age = 0
            if cat == ArtifactCategory.DEBUG and age < DEBUG_TTL_SEC and success:
                report.skipped.append(str(p))
                return
            if cat == ArtifactCategory.CACHE:
                report.skipped.append(str(p))
                return
            if cat == ArtifactCategory.TEMP or (cat == ArtifactCategory.DEBUG and age >= DEBUG_TTL_SEC):
                try:
                    p.unlink()
                    report.removed.append(str(p))
                except OSError:
                    report.skipped.append(str(p))
                return
            report.skipped.append(str(p))

        for root in roots:
            for p in list(root.rglob("*")):
                _maybe_delete(p)
            # Empty TEMP subdirs
            for sub in _TEMP_DIR_NAMES:
                d = root / sub
                if d.is_dir() and not any(d.rglob("*")):
                    try:
                        d.rmdir()
                    except OSError:
                        pass

        self.info["cleanup_manager_ran"] = True
        self.info["cleanup_manager_success"] = bool(success)
        self.info["cleanup_deleted_files"] = list(report.removed)
        self.info["cleanup_preserved_files"] = list(report.preserved) + list(report.blocked)
        logger.info(
            "[CLEANUP] session actor=%s success=%s deleted=%d preserved=%d blocked=%d",
            actor,
            success,
            len(report.removed),
            len(report.preserved),
            len(report.blocked),
        )
        return report


def safe_cleanup_temp_wavs(
    info: dict[str, Any],
    directories: list[Path],
    **kwargs: Any,
) -> dict[str, Any]:
    """Public API replacing ungated production_hardening.cleanup_temp_wavs for WAV."""
    mgr = CleanupManager(info)
    return mgr.cleanup_orphans(directories, **kwargs).to_dict()


def run_unified_cleanup(
    info: dict[str, Any] | None,
    *,
    session_dir: Path | str | None = None,
    success: bool = True,
    keep_studio: bool = False,
    keep_names: set[str] | None = None,
    mux_inputs_live: bool | None = None,
    actor: str = "cleanup_manager",
) -> dict[str, Any]:
    """Single gate used by other deleters (pipeline_cleanup / cleanup_engine)."""
    payload = info if isinstance(info, dict) else {}
    mgr = CleanupManager(payload)
    report = mgr.cleanup_session(
        session_dir,
        success=success,
        keep_studio=keep_studio,
        keep_names=keep_names,
        mux_inputs_live=mux_inputs_live,
        actor=actor,
    )
    return report.to_dict()
