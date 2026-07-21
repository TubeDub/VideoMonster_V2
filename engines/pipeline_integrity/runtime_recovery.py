"""Runtime Recovery — P3.1 §8 multi-location search before hard fail."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.runtime_registry import RuntimeRegistry, get_or_create_registry
from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref
from engines.pipeline_integrity.uuid_chain import ensure_segment_uuid


@dataclass
class RecoveryResult:
    recovered: bool
    path: str = ""
    source: str = ""
    searched: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovered": self.recovered,
            "path": self.path,
            "source": self.source,
            "searched": list(self.searched),
            "detail": self.detail,
        }


def _candidate_roots(info: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for key in (
        "session_dir",
        "artifacts_dir",
        "output_dir",
        "tts_cache_dir",
        "merge_cache_dir",
        "export_cache_dir",
    ):
        raw = info.get(key)
        if raw:
            roots.append(Path(str(raw)))
    # Common layout fallbacks
    task_id = str(info.get("task_id") or "")
    if task_id:
        base = Path(__file__).resolve().parents[2] / "output"
        roots.extend(
            [
                base / "sessions" / task_id,
                base / "diagnostics" / task_id,
                base / "slot_fit",
                base / "tts_cache",
                base / "merge_cache",
                base / "export_cache",
                base,
            ]
        )
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _find_by_uuid(roots: list[Path], uuid_token: str) -> Path | None:
    if not uuid_token or len(uuid_token) < 8:
        return None
    tokens = {uuid_token, uuid_token[:12], uuid_token[:8]}
    for root in roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in {".wav", ".mp3", ".ogg", ".flac", ".m4a"}:
                    continue
                name = p.name
                if any(t and t in name for t in tokens):
                    if p.stat().st_size > 0:
                        return p
        except OSError:
            continue
    return None


def recover_missing_audio(
    seg: dict[str, Any],
    info: dict[str, Any],
    *,
    registry: RuntimeRegistry | None = None,
) -> RecoveryResult:
    """
    Search order:
      registry → cache → temp → merge cache → export cache
    On hit: restore segment path reference. On miss: recovered=False.
    """
    searched: list[str] = []
    reg = registry or get_or_create_registry(info)
    suid = ensure_segment_uuid(seg)
    tts_uuid = str(seg.get("tts_uuid") or "")

    # 1) Registry
    searched.append("registry")
    rec = reg.get(suid) or (reg.get_by_tts_uuid(tts_uuid) if tts_uuid else None)
    if rec and rec.path and Path(rec.path).is_file() and Path(rec.path).stat().st_size > 0:
        seg["file"] = Path(rec.path).name
        seg["tts_file_path"] = Path(rec.path).name
        seg["runtime_registry_path"] = rec.path
        return RecoveryResult(True, path=rec.path, source="registry", searched=searched)

    roots = _candidate_roots(info)
    # 2–5) cache / temp / merge / export (all under candidate roots)
    for label, root in zip(
        ["cache", "temp", "merge_cache", "export_cache"] + ["extra"] * max(0, len(roots) - 4),
        roots,
    ):
        searched.append(f"{label}:{root}")

    found = _find_by_uuid(roots, tts_uuid) or _find_by_uuid(roots, suid)
    if found is None:
        # basename fallback from segment ref (display only)
        ref = resolve_segment_audio_ref(seg)
        if ref:
            for root in roots:
                cand = root / Path(ref).name
                searched.append(f"basename:{cand}")
                if cand.is_file() and cand.stat().st_size > 0:
                    found = cand
                    break

    if found is not None:
        abs_path = str(found.resolve())
        seg["file"] = found.name
        seg["tts_file_path"] = found.name
        seg["runtime_registry_path"] = abs_path
        reg.upsert_from_segment(seg, path=found, actor="recovery")
        return RecoveryResult(
            True, path=abs_path, source="filesystem_search", searched=searched
        )

    return RecoveryResult(
        False,
        source="",
        searched=searched,
        detail="audio not found in registry/cache/temp/merge/export",
    )
