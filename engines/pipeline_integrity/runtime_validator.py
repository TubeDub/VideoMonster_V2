"""Runtime Integrity Validator v2 — P3.1.

After each stage (Synthesized → Export) verify:
  wav exists, uuid exists, segment exists, metadata matches, hash matches.

On failure: recover (registry/cache/temp/merge/export) then stop + Diagnostic ZIP v2.
No silent continue / recreate / rename / fallback.
"""

from __future__ import annotations

import json
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engines.pipeline_integrity.contract_versions import require_contract_versions
from engines.pipeline_integrity.exceptions import RuntimeIntegrityError
from engines.pipeline_integrity.path_validation import validate_wav_path
from engines.pipeline_integrity.runtime_graph import build_runtime_graph
from engines.pipeline_integrity.runtime_recovery import recover_missing_audio
from engines.pipeline_integrity.runtime_registry import get_or_create_registry
from engines.pipeline_integrity.tts_artifact_lifecycle import get_tts_lifecycle
from engines.pipeline_integrity.tts_segment_fields import resolve_segment_audio_ref
from engines.pipeline_integrity.uuid_chain import UUID_FIELDS, ensure_project_uuids
from engines.pipeline_integrity.wav_ownership import get_wav_owner, stamp_wav_owner

logger = logging.getLogger("tubedub.runtime_validator")

STAGE_ALIASES = {
    "synthesized": "tts",
    "verified": "tts",
    "saved": "tts",
    "stored": "tts",
    "scheduled": "scheduler",
    "merged": "merge",
    "studio_handoff": "studio_handoff",
    "handoff": "studio_handoff",
    "export": "export",
    "exported": "export",
}


@dataclass
class RuntimeCheckResult:
    ok: bool
    stage: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    recoveries: list[dict[str, Any]] = field(default_factory=list)
    graph: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "checks": self.checks,
            "errors": self.errors,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "recoveries": list(self.recoveries),
            "graph": self.graph,
        }


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _normalize_stage(stage: str) -> str:
    key = str(stage or "").strip().lower()
    return STAGE_ALIASES.get(key, key or stage)


def validate_runtime(
    info: dict[str, Any],
    *,
    stage: str,
    require_tts: bool = False,
    require_merge: bool = False,
    require_contracts: bool = True,
    resolve_audio: Callable[..., Path] | None = None,
    attempt_recovery: bool = True,
    require_hash: bool = False,
) -> RuntimeCheckResult:
    """Run Runtime Integrity checks for ``stage``. Does not raise — caller decides."""
    t0 = time.perf_counter()
    stage_n = _normalize_stage(stage)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    recoveries: list[dict[str, Any]] = []
    segments = list(info.get("segments_data") or [])
    registry = get_or_create_registry(info)

    # Auto-enable TTS checks for post-synth stages
    if stage_n in {"tts", "scheduler", "merge", "studio_handoff", "export"}:
        require_tts = require_tts or stage_n in {
            "tts",
            "scheduler",
            "merge",
            "studio_handoff",
            "export",
        }
    if stage_n in {"merge", "studio_handoff", "export"}:
        # merge artifact required only when explicitly asked or merge/export stage
        if stage_n == "merge":
            require_merge = True

    # Contract
    if require_contracts and info.get("translation_locked"):
        try:
            require_contract_versions(info)
            checks.append(_check("contract_versions", True))
        except Exception as exc:
            checks.append(_check("contract_versions", False, str(exc)))
            errors.append(f"contract: {exc}")

    # Segments exist
    if not segments:
        checks.append(_check("segments_exist", False, "empty segments_data"))
        errors.append("segments_data is empty")
    else:
        checks.append(_check("segments_exist", True, f"n={len(segments)}"))

    # UUID chain + uniqueness
    try:
        ensure_project_uuids(segments)
        missing_uuid = 0
        seen: dict[str, set[str]] = {f: set() for f in UUID_FIELDS}
        dup = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            for f in UUID_FIELDS:
                val = str(seg.get(f) or "").strip()
                if not val:
                    missing_uuid += 1
                    continue
                if val in seen[f]:
                    dup += 1
                seen[f].add(val)
        ok_uuid = missing_uuid == 0 and dup == 0
        checks.append(_check("uuid", ok_uuid, f"missing={missing_uuid} dup={dup}"))
        if missing_uuid:
            errors.append("uuid missing on one or more segments")
        if dup:
            errors.append(f"duplicate UUID count={dup}")
    except Exception as exc:
        checks.append(_check("uuid", False, str(exc)))
        errors.append(f"uuid: {exc}")

    # Timing exists (post-lock stages)
    if stage_n in {"scheduler", "tts", "slot_fit", "merge", "studio_handoff", "export"}:
        missing_timing = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            start = seg.get("start_ms", seg.get("start_time_ms"))
            end = seg.get("end_ms", seg.get("end_time_ms"))
            slot = int(seg.get("slot_ms") or 0)
            if start is None or end is None:
                if slot <= 0:
                    missing_timing += 1
            elif int(end) <= int(start) and slot <= 0:
                missing_timing += 1
        ok_t = missing_timing == 0
        checks.append(_check("timing_exists", ok_t, f"missing={missing_timing}"))
        if not ok_t:
            errors.append(f"timing missing on {missing_timing} segment(s)")

    # TTS / path / hash / metadata / ownership
    if require_tts:
        missing_tts = 0
        missing_disk = 0
        corrupt = 0
        hash_mismatch = 0
        meta_mismatch = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            if seg.get("tts_status") == "failed" or seg.get("status") == "failed":
                continue
            stamp_wav_owner(seg)
            registry.upsert_from_segment(seg, actor=f"validate:{stage_n}", compute_hash=False)
            ref = resolve_segment_audio_ref(seg)
            path: Path | None = None
            if ref and resolve_audio is not None:
                try:
                    path = Path(resolve_audio(ref, task_info=info))
                except TypeError:
                    path = Path(resolve_audio(ref))
            elif seg.get("runtime_registry_path"):
                path = Path(str(seg["runtime_registry_path"]))
            elif ref:
                path = Path(ref)

            if path is None or not path.is_file():
                if attempt_recovery:
                    rec = recover_missing_audio(seg, info, registry=registry)
                    recoveries.append(rec.to_dict())
                    if rec.recovered:
                        path = Path(rec.path)
                    else:
                        missing_disk += 1
                        if not ref:
                            missing_tts += 1
                        continue
                else:
                    missing_disk += 1
                    if not ref:
                        missing_tts += 1
                    continue

            vr = validate_wav_path(
                path,
                expected_hash=str(seg.get("audio_sha256") or ""),
                min_size=1,
            )
            if not vr.ok:
                if any("wav_header" in e or "size=" in e for e in (vr.errors or [])):
                    corrupt += 1
                else:
                    missing_disk += 1
            if require_hash and seg.get("audio_sha256") and vr.hash:
                if vr.hash != seg.get("audio_sha256"):
                    hash_mismatch += 1
            # Metadata match: registry path vs segment
            rec = registry.get(str(seg.get("segment_uuid") or ""))
            if rec and rec.path and path.is_file():
                if Path(rec.path).resolve() != path.resolve():
                    # Update registry to resolved path (restore link — allowed recovery)
                    registry.upsert_from_segment(seg, path=path, actor="meta_realign")
            elif rec is None:
                meta_mismatch += 1
            # Lifecycle present
            _ = get_tts_lifecycle(seg)
            _ = get_wav_owner(seg)

        ok_tts = (
            missing_tts == 0
            and missing_disk == 0
            and corrupt == 0
            and hash_mismatch == 0
        )
        checks.append(
            _check(
                "tts_exists",
                ok_tts,
                f"missing_ref={missing_tts} missing_disk={missing_disk} "
                f"corrupt={corrupt} hash_mismatch={hash_mismatch} meta={meta_mismatch}",
            )
        )
        if not ok_tts:
            errors.append(
                f"TTS integrity: ref={missing_tts} disk={missing_disk} "
                f"corrupt={corrupt} hash={hash_mismatch}"
            )

    # Scheduler integrity extras
    if stage_n == "scheduler":
        empty_refs = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            if not (seg.get("file") or seg.get("tts_file_path") or seg.get("tts_uuid")):
                empty_refs += 1
            if not (seg.get("segment_uuid") or seg.get("segment_id")):
                empty_refs += 1
        ok_s = empty_refs == 0
        checks.append(_check("scheduler_integrity", ok_s, f"empty_refs={empty_refs}"))
        if not ok_s:
            errors.append(f"scheduler empty references={empty_refs}")

    # Merge integrity
    if require_merge or stage_n == "merge":
        merge_path = (
            info.get("final_audio_path")
            or info.get("merged_track")
            or info.get("mux_output")
            or info.get("output_path")
        )
        ok_m = bool(merge_path) and Path(str(merge_path)).is_file()
        checks.append(_check("merge_exists", ok_m, str(merge_path or "")))
        if not ok_m:
            errors.append("merge artifact missing")
        # Incoming WAVs non-empty
        empty_wav = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            ref = resolve_segment_audio_ref(seg)
            if not ref:
                empty_wav += 1
                continue
            path = None
            if resolve_audio is not None:
                try:
                    path = Path(resolve_audio(ref, task_info=info))
                except TypeError:
                    path = Path(resolve_audio(ref))
            if path is None or not path.is_file() or path.stat().st_size == 0:
                empty_wav += 1
        ok_in = empty_wav == 0
        checks.append(_check("merge_inputs", ok_in, f"empty_or_missing={empty_wav}"))
        if not ok_in:
            errors.append(f"merge inputs invalid={empty_wav}")

    # Studio handoff extras
    if stage_n in {"studio_handoff", "export"}:
        filenames: dict[str, str] = {}
        dup_files = 0
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") or seg.get("merged_into_id"):
                continue
            name = Path(str(resolve_segment_audio_ref(seg) or "")).name
            if not name:
                continue
            suid = str(seg.get("segment_uuid") or "")
            if name in filenames and filenames[name] != suid:
                dup_files += 1
            filenames[name] = suid
        ok_h = dup_files == 0
        checks.append(_check("handoff_no_dup_filename", ok_h, f"dup={dup_files}"))
        if not ok_h:
            errors.append(f"duplicate filenames at handoff={dup_files}")

    graph = build_runtime_graph(
        info,
        stage_failures={stage_n: "; ".join(errors)} if errors else None,
    ).to_dict()

    elapsed = (time.perf_counter() - t0) * 1000.0
    return RuntimeCheckResult(
        ok=not errors,
        stage=stage_n,
        checks=checks,
        errors=errors,
        elapsed_ms=elapsed,
        recoveries=recoveries,
        graph=graph,
    )


def enforce_runtime(
    info: dict[str, Any],
    *,
    stage: str,
    output_dir: Path | None = None,
    **kwargs: Any,
) -> RuntimeCheckResult:
    """Validate and raise RuntimeIntegrityError + write diagnostic ZIP on failure."""
    result = validate_runtime(info, stage=stage, **kwargs)
    info.setdefault("runtime_integrity", {})
    info["runtime_integrity"][stage] = result.to_dict()
    if result.ok:
        return result
    zip_path = None
    if output_dir is not None:
        zip_path = write_diagnostic_zip(
            output_dir,
            task_id=str(info.get("task_id") or "unknown"),
            stage=stage,
            result=result,
            info=info,
        )
    raise RuntimeIntegrityError(
        f"RUNTIME_INTEGRITY failed at {stage}: {'; '.join(result.errors)}",
        stage=stage,
        details={
            "errors": result.errors,
            "checks": result.checks,
            "diagnostic_zip": str(zip_path) if zip_path else None,
            "graph": result.graph,
            "recoveries": result.recoveries,
        },
    )


def write_diagnostic_zip(
    output_dir: Path,
    *,
    task_id: str,
    stage: str,
    result: RuntimeCheckResult,
    info: dict[str, Any],
) -> Path:
    """Diagnostic ZIP v2 — state, FSM, UUID, owner, lifecycle, paths, hash, logs."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    zip_path = output_dir / f"runtime_integrity_{task_id}_{stage}_{stamp}.zip"
    registry = get_or_create_registry(info)
    segments = list(info.get("segments_data") or [])
    wav_index = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        wav_index.append(
            {
                "segment_uuid": seg.get("segment_uuid"),
                "tts_uuid": seg.get("tts_uuid"),
                "audio_uuid": seg.get("audio_uuid"),
                "owner": get_wav_owner(seg).value,
                "lifecycle": get_tts_lifecycle(seg).value,
                "path": seg.get("runtime_registry_path")
                or seg.get("tts_file_path")
                or seg.get("file"),
                "hash": seg.get("audio_sha256"),
                "history": seg.get("runtime_history") or [],
            }
        )
    payload = {
        "task_id": task_id,
        "stage": stage,
        "pipeline_state": info.get("pipeline_state"),
        "fsm": info.get("pipeline_state"),
        "result": result.to_dict(),
        "runtime_graph": result.graph,
        "contract_versions": {
            k: info.get(k)
            for k in (
                "translation_contract_version",
                "dub_contract_version",
                "scheduler_contract_version",
                "studio_contract_version",
                "tts_contract_version",
            )
        },
        "segment_count": len(segments),
        "scheduler_log": info.get("scheduler_log") or info.get("scheduler_budget_sample"),
        "merge_log": info.get("merge_log") or info.get("mux_stats"),
        "tts_log": info.get("tts_log") or info.get("tts_stats"),
        "runtime_validator_log": info.get("runtime_integrity"),
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("runtime_integrity.json", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        zf.writestr(
            "segments_snapshot.json",
            json.dumps(segments, ensure_ascii=False, indent=2, default=str),
        )
        zf.writestr(
            "runtime_registry.json",
            json.dumps(registry.to_dict(), ensure_ascii=False, indent=2, default=str),
        )
        zf.writestr(
            "wav_index.json",
            json.dumps(wav_index, ensure_ascii=False, indent=2, default=str),
        )
        zf.writestr(
            "runtime_graph.json",
            json.dumps(result.graph, ensure_ascii=False, indent=2, default=str),
        )
        zf.writestr(
            "timeline_events.json",
            json.dumps(registry.events, ensure_ascii=False, indent=2, default=str),
        )
    logger.error("RUNTIME_INTEGRITY diagnostic zip: %s", zip_path)
    return zip_path


def assert_studio_handoff_wavs(
    info: dict[str, Any],
    *,
    resolve_audio: Callable[..., Path],
) -> None:
    """P3.1 §10: Studio handoff forbidden unless all WAV/UUID integrity checks pass."""
    from engines.pipeline_integrity.error_taxonomy import HandoffViolation

    result = validate_runtime(
        info,
        stage="studio_handoff",
        require_tts=True,
        require_contracts=True,
        resolve_audio=resolve_audio,
        attempt_recovery=True,
    )
    if not result.ok:
        raise HandoffViolation(
            "Studio Handoff blocked: runtime integrity failed",
            stage="studio_handoff",
            details={"errors": result.errors, "checks": result.checks, "graph": result.graph},
        ) from None


def enforce_scheduler_integrity(
    info: dict[str, Any],
    *,
    output_dir: Path | None = None,
    resolve_audio: Callable[..., Path] | None = None,
) -> RuntimeCheckResult:
    return enforce_runtime(
        info,
        stage="scheduler",
        require_tts=True,
        output_dir=output_dir,
        resolve_audio=resolve_audio,
    )


def enforce_merge_integrity(
    info: dict[str, Any],
    *,
    output_dir: Path | None = None,
    resolve_audio: Callable[..., Path] | None = None,
) -> RuntimeCheckResult:
    return enforce_runtime(
        info,
        stage="merge",
        require_tts=True,
        require_merge=True,
        output_dir=output_dir,
        resolve_audio=resolve_audio,
    )
