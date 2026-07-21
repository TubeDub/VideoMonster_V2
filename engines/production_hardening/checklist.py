"""P16.8 / P16.9 / P16.10 — Release checklist + RC scenario stubs."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.audio_timing_optimizer import optimize_audio_timing
from engines.pipeline_integrity.crash_recovery import save_checkpoint, load_checkpoint
from engines.pipeline_integrity.golden_dataset import (
    assert_matches_golden,
    ensure_golden_layout,
)
from engines.pipeline_integrity.observability import health_dashboard
from engines.production_hardening.concurrency import run_concurrency_harness
from engines.production_hardening.fault_injection import run_fault_suite
from engines.production_hardening.long_run import run_long_run
from engines.production_hardening.resource_manager import take_resource_snapshot


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ChecklistItem:
    name: str
    ok: bool
    detail: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


@dataclass
class ChecklistResult:
    ok: bool
    items: list[ChecklistItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "items": [i.to_dict() for i in self.items]}


RC_SCENARIOS = (
    "long_film",
    "short_clip",
    "interview",
    "podcast",
    "cartoon",
    "youtube",
    "multilang",
)


def _run_pytest(paths: list[str], timeout: int = 300) -> ChecklistItem:
    t0 = time.perf_counter()
    cmd = [sys.executable, "-m", "pytest", *paths, "-q", "--tb=line"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "VM_DEV_MODE": "1"},
        )
        ok = proc.returncode == 0
        detail = (proc.stdout or "")[-500:]
        if not ok:
            detail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-800:]
    except Exception as exc:
        ok = False
        detail = str(exc)
    return ChecklistItem(
        name="pytest:" + ",".join(Path(p).name for p in paths),
        ok=ok,
        detail=detail,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def run_rc_scenarios() -> ChecklistItem:
    """Synthetic RC content shapes — deterministic optimizer fingerprints."""
    t0 = time.perf_counter()
    failures = []
    for name in RC_SCENARIOS:
        n = {"long_film": 200, "short_clip": 8, "interview": 40, "podcast": 60,
             "cartoon": 30, "youtube": 25, "multilang": 15}.get(name, 20)
        rows = []
        t = 0
        for i in range(n):
            dur = 500 + (i * 13) % 700
            rows.append(
                {
                    "segment_id": f"rc-{name}-{i:04d}",
                    "translated_text": f"{name}-{i}",
                    "text": f"{name}-{i}",
                    "start_ms": t,
                    "end_ms": t + dur,
                    "slot_ms": dur,
                    "playback_duration": dur + (i % 40),
                    "translation_locked": True,
                }
            )
            t += dur + 10
        try:
            optimize_audio_timing(rows, settings={"scenario": name})
        except Exception as exc:
            failures.append(f"{name}:{exc}")
    return ChecklistItem(
        name="release_candidate_scenarios",
        ok=not failures,
        detail=";".join(failures) or f"scenarios={len(RC_SCENARIOS)}",
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def run_release_checklist(
    *,
    include_pytest: bool = True,
    long_run_sec: float = 3.0,
    work_dir: Path | None = None,
) -> ChecklistResult:
    work_dir = work_dir or (ROOT / "output" / "p16_hardening")
    work_dir.mkdir(parents=True, exist_ok=True)
    items: list[ChecklistItem] = []

    if include_pytest:
        items.append(
            _run_pytest(
                [
                    "tests/test_translation_lock_p0.py",
                    "tests/test_scheduler_p1.py",
                    "tests/test_dub_engine_architecture_p1.py",
                    "tests/test_dub_engine_import_lint_v2.py",
                    "tests/test_dub_engine_tz_v2.py",
                    "tests/test_audio_timing_optimizer_p2.py",
                    "tests/test_perf_budget_p5.py",
                ]
            )
        )

    # Fault injection
    t0 = time.perf_counter()
    faults = run_fault_suite(work_dir / "faults")
    items.append(
        ChecklistItem(
            "fault_injection",
            ok=faults.ok,
            detail=f"cases={len(faults.cases)}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    # Concurrency
    t0 = time.perf_counter()
    conc = run_concurrency_harness(projects=6, segments_per_project=15, workers=4)
    items.append(
        ChecklistItem(
            "concurrency",
            ok=conc.ok,
            detail=conc.detail,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    # Long run (short by default)
    t0 = time.perf_counter()
    lr = run_long_run(duration_sec=long_run_sec, segments_per_iter=30, projects_parallel=3)
    items.append(
        ChecklistItem(
            "long_run_stability",
            ok=lr.ok,
            detail=lr.detail + f" iters={lr.iterations}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    # Crash recovery
    t0 = time.perf_counter()
    info = {
        "task_id": "p16-rc",
        "pipeline_state": "LOCKED",
        "translation_locked": True,
        "segments_data": [{"segment_id": "s1", "file": "a.wav"}],
    }
    from engines.pipeline_integrity.contract_versions import stamp_contract_versions

    stamp_contract_versions(info)
    cp = save_checkpoint(work_dir / "ckpt", info, stage="lock")
    loaded = load_checkpoint(work_dir / "ckpt")
    items.append(
        ChecklistItem(
            "crash_recovery",
            ok=bool(loaded) and cp.is_file(),
            detail=str(cp),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    # Golden / deterministic
    t0 = time.perf_counter()
    groot = ensure_golden_layout(work_dir / "golden")
    segs = [
        {"segment_id": "g1", "start_ms": 0, "end_ms": 100, "translated_text": "a"},
        {"segment_id": "g2", "start_ms": 100, "end_ms": 200, "translated_text": "b"},
    ]
    try:
        assert_matches_golden("p16_release", segs, settings={"v": 1}, root=groot)
        gold_ok = True
        gold_detail = "ok"
    except Exception as exc:
        gold_ok = False
        gold_detail = str(exc)
    items.append(
        ChecklistItem(
            "golden_deterministic",
            ok=gold_ok,
            detail=gold_detail,
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    # Observability + resources
    snap = take_resource_snapshot(temp_dirs=[work_dir])
    dash = health_dashboard(info)
    items.append(
        ChecklistItem(
            "observability_resources",
            ok=bool(dash.get("execution_graph")) and snap.ts > 0,
            detail=f"rss_mb={snap.rss_mb} threads={snap.threads}",
        )
    )

    items.append(run_rc_scenarios())

    # Backward compatibility
    t0 = time.perf_counter()
    from engines.production_hardening.backcompat import check_backward_compatibility

    legacy = work_dir / "legacy_openddf.json"
    legacy.write_text(
        json.dumps({"task_id": "legacy", "segments": [], "summary": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    bc = check_backward_compatibility([legacy])
    items.append(
        ChecklistItem(
            "backward_compatibility",
            ok=bool(bc.get("ok")),
            detail=f"checked={bc.get('checked')}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    # Final acceptance aggregate
    critical_ok = all(i.ok for i in items)
    items.append(
        ChecklistItem(
            "final_acceptance",
            ok=critical_ok,
            detail="all checklist items green" if critical_ok else "failures present",
        )
    )
    return ChecklistResult(ok=critical_ok, items=items)
