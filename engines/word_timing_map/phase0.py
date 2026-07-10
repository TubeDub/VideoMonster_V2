"""Phase 0 — Word Timing Map checkpoints (zero behavior change, data integrity)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.word_timing_map.pipeline import word_maps_from_task_info

logger = logging.getLogger("tubedub.word_timing_map.phase0")

PHASE0_STAGES = (
    "post_merge",
    "post_translate",
    "post_sso",
    "pre_tts",
    "final",
)


@dataclass
class WtmCheckpoint:
    stage: str
    segment_count: int = 0
    words_total: int = 0
    words_per_segment: list[int] = field(default_factory=list)
    real_segments: int = 0
    estimated_segments: int = 0
    ok: bool = True
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "segment_count": self.segment_count,
            "words_total": self.words_total,
            "words_per_segment": self.words_per_segment,
            "real_segments": self.real_segments,
            "estimated_segments": self.estimated_segments,
            "ok": self.ok,
            "issues": self.issues,
        }


def snapshot_from_info(info: dict[str, Any], stage: str) -> WtmCheckpoint:
    maps = word_maps_from_task_info(info)
    words_per = [len(m.words) for m in maps]
    real = sum(1 for m in maps if m.timing_source == "real")
    return WtmCheckpoint(
        stage=stage,
        segment_count=len(maps),
        words_total=sum(words_per),
        words_per_segment=words_per,
        real_segments=real,
        estimated_segments=len(maps) - real,
        ok=True,
    )


def _compare_to_baseline(current: WtmCheckpoint, baseline: WtmCheckpoint) -> list[str]:
    issues: list[str] = []
    if current.segment_count != baseline.segment_count:
        issues.append(
            f"segment_count:{baseline.segment_count}->{current.segment_count}"
        )
    if current.words_total != baseline.words_total:
        issues.append(f"words_total:{baseline.words_total}->{current.words_total}")
    if current.words_per_segment != baseline.words_per_segment:
        issues.append("words_per_segment_changed")
    if current.real_segments != baseline.real_segments:
        issues.append(
            f"real_segments:{baseline.real_segments}->{current.real_segments}"
        )
    return issues


class WtmCheckpointLog:
    """Records WTM integrity at each pipeline stage (Phase 0 gate)."""

    def __init__(self, task_id: str, app_dir: Path):
        self.task_id = task_id
        self.app_dir = app_dir
        self.baseline: WtmCheckpoint | None = None
        self.checkpoints: list[WtmCheckpoint] = []

    def record(self, info: dict[str, Any], stage: str) -> WtmCheckpoint:
        cp = snapshot_from_info(info, stage)
        if self.baseline is None:
            self.baseline = cp
        else:
            cp.issues = _compare_to_baseline(cp, self.baseline)
            cp.ok = len(cp.issues) == 0
        self.checkpoints.append(cp)
        if not cp.ok:
            logger.warning(
                "[WTM Phase0] checkpoint FAIL stage=%s issues=%s",
                stage,
                cp.issues,
            )
        else:
            logger.debug("[WTM Phase0] checkpoint OK stage=%s words=%d", stage, cp.words_total)
        return cp

    def all_ok(self) -> bool:
        return all(c.ok for c in self.checkpoints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "all_ok": self.all_ok(),
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "checkpoints": [c.to_dict() for c in self.checkpoints],
        }

    def flush(self) -> str:
        out_dir = self.app_dir / "output" / "dev" / "word_timing_map"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"wtm_phase0_{self.task_id}.json"
        payload = {
            **self.to_dict(),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        text_path = out_dir / f"wtm_phase0_{self.task_id}.txt"
        lines = [
            f"Word Timing Map — Phase 0 checkpoints task={self.task_id}",
            f"all_ok={self.all_ok()}",
            "",
        ]
        for cp in self.checkpoints:
            status = "OK" if cp.ok else "FAIL"
            lines.append(
                f"[{status}] {cp.stage}: segments={cp.segment_count} "
                f"words={cp.words_total} real={cp.real_segments} est={cp.estimated_segments}"
            )
            for issue in cp.issues:
                lines.append(f"  ! {issue}")
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)


def format_dev_inspector_block(info: dict[str, Any]) -> str:
    """Human-readable WTM summary for developer diagnostics."""
    maps = word_maps_from_task_info(info)
    if not maps:
        return "Word Timing Map: (empty)"
    meta = info.get("word_timing_meta") or {}
    lines = [
        f"Word Timing Map: {len(maps)} segments, {meta.get('words_total', '?')} words",
        f"  real={meta.get('real_segments', 0)} estimated={meta.get('estimated_segments', 0)}",
        f"  sync_mode={info.get('wtm_sync_mode', 'legacy')} phase={info.get('wtm_phase', 'phase0')}",
    ]
    cps = (info.get("word_timing_checkpoints") or {}).get("checkpoints") or []
    for cp in cps[-5:]:
        flag = "OK" if cp.get("ok") else "FAIL"
        lines.append(
            f"  [{flag}] {cp.get('stage')}: {cp.get('words_total')} words"
        )
    if maps:
        sample = maps[0]
        words_preview = " | ".join(
            f"{w.text}@{w.start_ms}-{w.end_ms}" for w in sample.words[:8]
        )
        if len(sample.words) > 8:
            words_preview += " …"
        lines.append(
            f"  seg0 [{sample.timing_source}]: {words_preview}"
        )
    return "\n".join(lines)
