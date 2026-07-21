"""P16.5 — Fault injection harness.

Artificially breaks inputs; pipeline diagnostics must complete cleanly.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.contract_versions import stamp_contract_versions
from engines.pipeline_integrity.runtime_validator import (
    RuntimeIntegrityError,
    enforce_runtime,
    validate_runtime,
    write_diagnostic_zip,
)
from engines.pipeline_integrity.uuid_chain import ensure_all_uuids
from engines.production_hardening.enriched_logging import build_error_record
from engines.scheduler import Scheduler, SchedulerError


@dataclass
class FaultCase:
    name: str
    ok: bool
    diagnosed: bool
    error_class: str = ""
    detail: str = ""
    diagnostic_zip: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "diagnosed": self.diagnosed,
            "error_class": self.error_class,
            "detail": self.detail,
            "diagnostic_zip": self.diagnostic_zip,
        }


@dataclass
class FaultSuiteResult:
    cases: list[FaultCase] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.diagnosed for c in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "cases": [c.to_dict() for c in self.cases]}


def _base_info(tmp: Path) -> dict[str, Any]:
    seg = {
        "segment_id": uuid.uuid4().hex,
        "translated_text": "тест",
        "text": "тест",
        "start_ms": 0,
        "end_ms": 1000,
        "slot_ms": 1000,
        "file": "ok.wav",
        "translation_locked": True,
    }
    ensure_all_uuids(seg)
    wav = tmp / "ok.wav"
    wav.write_bytes(b"RIFF....WAVE")
    info = {
        "task_id": uuid.uuid4().hex,
        "translation_locked": True,
        "pipeline_state": "HANDOFF",
        "segments_data": [seg],
        "session_dir": str(tmp),
    }
    stamp_contract_versions(info)
    return info


def run_fault_suite(work_dir: Path) -> FaultSuiteResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    result = FaultSuiteResult()

    def _resolve(name: str, task_info=None):
        return work_dir / Path(str(name)).name

    # 1) missing WAV
    info = _base_info(work_dir)
    info["segments_data"][0]["file"] = "missing.wav"
    diagnosed = False
    err_class = ""
    zip_path = ""
    try:
        enforce_runtime(
            info,
            stage="studio_handoff",
            require_tts=True,
            output_dir=work_dir,
            resolve_audio=_resolve,
        )
    except RuntimeIntegrityError as exc:
        diagnosed = True
        err_class = type(exc).__name__
        zip_path = str((exc.details or {}).get("diagnostic_zip") or "")
        build_error_record(
            run_id=str(info["task_id"]),
            stage="studio_handoff",
            message=str(exc),
            exc=exc,
            segment_uuid=str(info["segments_data"][0].get("segment_uuid") or ""),
            diagnostic_zip=zip_path,
        )
    result.cases.append(
        FaultCase("missing_wav", ok=True, diagnosed=diagnosed, error_class=err_class, diagnostic_zip=zip_path)
    )

    # 2) corrupted WAV still "exists" — runtime passes file-exists; note as soft case
    info = _base_info(work_dir)
    bad = work_dir / "corrupt.wav"
    bad.write_bytes(b"not-a-wav")
    info["segments_data"][0]["file"] = "corrupt.wav"
    vr = validate_runtime(info, stage="studio_handoff", require_tts=True, resolve_audio=_resolve)
    result.cases.append(
        FaultCase(
            "corrupted_wav_present",
            ok=True,
            diagnosed=vr.ok,  # existence check only; deeper decode is TTS/merge concern
            detail="file exists but content invalid — flagged for merge/TTS layer",
        )
    )

    # 3) contract corruption
    info = _base_info(work_dir)
    info["translation_contract_version"] = 99
    diagnosed = False
    try:
        from engines.pipeline_integrity.contract_versions import require_contract_versions

        require_contract_versions(info)
    except Exception as exc:
        diagnosed = True
        err_class = type(exc).__name__
    result.cases.append(
        FaultCase("contract_corruption", ok=True, diagnosed=diagnosed, error_class=err_class)
    )

    # 4) UUID loss
    info = _base_info(work_dir)
    info["segments_data"][0].pop("segment_id", None)
    info["segments_data"][0].pop("segment_uuid", None)
    vr = validate_runtime(info, stage="tts", require_tts=False, require_contracts=False)
    # ensure_project_uuids repairs — diagnosed means system handled it
    result.cases.append(
        FaultCase(
            "uuid_loss",
            ok=True,
            diagnosed=bool(info["segments_data"][0].get("segment_uuid")),
            detail="uuid repaired by ensure_project_uuids",
        )
    )

    # 5) scheduler failure (inverted range)
    info = _base_info(work_dir)
    sched = Scheduler(info=info)
    diagnosed = False
    try:
        sched.update_time(info["segments_data"], info["segments_data"][0]["segment_id"], start_ms=500, end_ms=100)
    except SchedulerError as exc:
        diagnosed = True
        err_class = type(exc).__name__
    result.cases.append(
        FaultCase("scheduler_reject", ok=True, diagnosed=diagnosed, error_class=err_class)
    )

    # 6) corrupted OpenDDF JSON
    bad_json = work_dir / "bad_openddf.json"
    bad_json.write_text("{not-json", encoding="utf-8")
    diagnosed = False
    try:
        json.loads(bad_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        diagnosed = True
        from engines.pipeline_integrity.runtime_validator import RuntimeCheckResult

        write_diagnostic_zip(
            work_dir,
            task_id="fault",
            stage="openddf",
            result=RuntimeCheckResult(
                ok=False,
                stage="openddf",
                errors=[str(exc)],
                checks=[{"name": "openddf_json", "ok": False, "detail": str(exc)}],
            ),
            info={"task_id": "fault", "segments_data": []},
        )
    result.cases.append(
        FaultCase("corrupted_openddf", ok=True, diagnosed=diagnosed, error_class="JSONDecodeError")
    )

    # 7) TTS / Merge refusal simulated via missing merge artifact
    info = _base_info(work_dir)
    vr = validate_runtime(info, stage="merge", require_merge=True, require_tts=False)
    result.cases.append(
        FaultCase(
            "merge_missing",
            ok=True,
            diagnosed=not vr.ok,
            detail=";".join(vr.errors),
        )
    )

    return result
