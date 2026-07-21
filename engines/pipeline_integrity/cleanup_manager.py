"""Cleanup Manager — P3.1 §2/§14. No direct WAV deletes; FSM/owner/refcount gated."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    WavOwner,
    assert_cleanup_allowed,
    get_wav_owner,
)

logger = logging.getLogger("tubedub.cleanup_manager")


@dataclass
class CleanupReport:
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "removed": list(self.removed),
            "skipped": list(self.skipped),
            "blocked": list(self.blocked),
        }


class CleanupManager:
    """Only pathway allowed to delete registered WAV artifacts."""

    def __init__(self, info: dict[str, Any], registry: RuntimeRegistry | None = None) -> None:
        self.info = info
        self.registry = registry or get_or_create_registry(info)

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
        """
        import time

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
            # Block cleanup of active lifecycle artifacts
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
        for root in directories:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in {".wav", ".mp3", ".ogg", ".tmp"}:
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
                # Only delete orphans (not in live set) OR fully exported pipeline
                if p.name in live_names and allow_registered:
                    # Still require RELEASED-ish — skip live names unless force export cleanup
                    report.blocked.append(str(p))
                    continue
                try:
                    p.unlink()
                    report.removed.append(str(p))
                except OSError:
                    report.skipped.append(str(p))
        return report


def safe_cleanup_temp_wavs(
    info: dict[str, Any],
    directories: list[Path],
    **kwargs: Any,
) -> dict[str, Any]:
    """Public API replacing ungated production_hardening.cleanup_temp_wavs for WAV."""
    mgr = CleanupManager(info)
    return mgr.cleanup_orphans(directories, **kwargs).to_dict()
