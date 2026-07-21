"""P17.1 — Golden Release baseline store."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.audio_timing_optimizer import optimize_audio_timing
from engines.pipeline_integrity.golden_dataset import ensure_golden_layout, golden_root
from engines.release_governance.config_freeze import (
    collect_frozen_config,
    write_config_freeze,
)
from engines.release_governance.versions import collect_version_bundle

DEFAULT_RELEASES_ROOT = Path(__file__).resolve().parents[2] / "releases"


@dataclass
class QualityMetrics:
    overflow_count: int = 0
    overlap_count: int = 0
    sync_score: float = 1.0  # 1.0 = perfect (lower is worse)
    translation_quality_score: float = 1.0
    processing_ms: float = 0.0
    deterministic_fingerprint: str = ""
    runtime_integrity_errors: int = 0
    segment_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "overflow_count": self.overflow_count,
            "overlap_count": self.overlap_count,
            "sync_score": self.sync_score,
            "translation_quality_score": self.translation_quality_score,
            "processing_ms": round(self.processing_ms, 3),
            "deterministic_fingerprint": self.deterministic_fingerprint,
            "runtime_integrity_errors": self.runtime_integrity_errors,
            "segment_count": self.segment_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualityMetrics":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def _synthetic_segments(n: int = 40, seed: str = "golden") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    t = 0
    for i in range(n):
        dur = 450 + (i * 19) % 550
        rows.append(
            {
                "segment_id": f"{seed}-{i:04d}",
                "translated_text": f"{seed}-line-{i}",
                "text": f"{seed}-line-{i}",
                "start_ms": t,
                "end_ms": t + dur,
                "slot_ms": dur,
                "playback_duration": dur + (i % 35),
                "translation_locked": True,
            }
        )
        t += dur + 15
    return rows


def measure_candidate_quality(
    segments: list[dict[str, Any]] | None = None,
    *,
    settings: dict[str, Any] | None = None,
) -> QualityMetrics:
    segs = list(segments or _synthetic_segments())
    t0 = time.perf_counter()
    result = optimize_audio_timing(segs, settings=settings or {"p17": True})
    elapsed = (time.perf_counter() - t0) * 1000.0
    metrics = result.metrics
    # Sync score: inverse of overflow/overlap pressure (clamped).
    pressure = metrics.overflow_count + metrics.overlap_count
    sync = max(0.0, 1.0 - (pressure / max(1, len(segs))))
    return QualityMetrics(
        overflow_count=int(metrics.overflow_count),
        overlap_count=int(metrics.overlap_count),
        sync_score=round(sync, 4),
        translation_quality_score=1.0,  # text locked — baseline identity
        processing_ms=elapsed,
        deterministic_fingerprint=result.fingerprint,
        runtime_integrity_errors=0,
        segment_count=len(segs),
    )


def releases_root(root: Path | None = None) -> Path:
    path = Path(root) if root else DEFAULT_RELEASES_ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


def promote_golden_release(
    *,
    label: str = "latest",
    root: Path | None = None,
    metrics: QualityMetrics | None = None,
    regression_report: dict[str, Any] | None = None,
    architecture_report: dict[str, Any] | None = None,
) -> Path:
    """
    Persist a successful release as the Golden Release baseline.

    Stores versions, golden dataset pointer, budgets, regression & architecture reports.
    """
    base = releases_root(root) / f"golden_{label}"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    versions = collect_version_bundle()
    quality = (metrics or measure_candidate_quality()).to_dict()
    write_config_freeze(base / "config_freeze.json")

    # Snapshot golden dataset layout (manifest + fingerprints)
    gsrc = ensure_golden_layout(golden_root())
    gdst = base / "golden_dataset"
    shutil.copytree(gsrc, gdst, dirs_exist_ok=True)

    payload = {
        "label": label,
        "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "versions": versions,
        "quality_metrics": quality,
        "performance_budget": versions.get("performance_budgets_ms"),
        "regression_report": regression_report
        or {"status": "baseline", "notes": "initial golden promote"},
        "architecture_report": architecture_report
        or {"status": "baseline", "notes": "initial golden promote"},
        "config_freeze": "config_freeze.json",
        "golden_dataset": "golden_dataset",
    }
    (base / "golden_release.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Convenience pointer (avoid case collision with golden_latest/ on Windows)
    latest = releases_root(root) / "CURRENT_GOLDEN.txt"
    latest.write_text(str(base.resolve()), encoding="utf-8")
    return base


@dataclass
class GoldenRelease:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def metrics(self) -> QualityMetrics:
        return QualityMetrics.from_dict(self.data.get("quality_metrics") or {})

    @property
    def versions(self) -> dict[str, Any]:
        return dict(self.data.get("versions") or {})


def load_golden_release(
    *,
    label: str = "latest",
    root: Path | None = None,
) -> GoldenRelease | None:
    base = releases_root(root) / f"golden_{label}"
    meta = base / "golden_release.json"
    if not meta.is_file():
        # Try pointer
        ptr = releases_root(root) / "CURRENT_GOLDEN.txt"
        if ptr.is_file():
            base = Path(ptr.read_text(encoding="utf-8").strip())
            meta = base / "golden_release.json"
        if not meta.is_file():
            return None
    data = json.loads(meta.read_text(encoding="utf-8"))
    return GoldenRelease(path=base, data=data)
