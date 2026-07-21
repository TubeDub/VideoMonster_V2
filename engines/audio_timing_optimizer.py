"""AudioTimingOptimizer — MASTER TZ v3.0 P9.

Text Fit → Audio Fit. After TRANSLATION LOCK text is never changed.

Levels (in order):
  1 Trim Silence
  2 Smart Pause / Redistribute Gap
  3 Tempo 5%
  4 Tempo 10% (emergency when overflow >15%)
  5 Micro Stretch
  6 Borrow Gap / Neighbor Redistribution
  7 Crossfade
  8 Overflow Manager (state, not error)
  9 Underflow Manager (pause/padding — never text expand)

Deterministic: same segments + same settings → same result.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.audio_timing_optimizer")

TEMPO_MIN = 0.95  # TZ v4.0 P2: ±5% only after LOCK
TEMPO_MAX = 1.05
TEMPO_EMERGENCY_MAX = 1.12  # red overflow >15% after DSAL exhausted
MICRO_STRETCH_MIN = 0.95
MICRO_STRETCH_MAX = 1.05
CROSSFADE_MS_DEFAULT = 12
BORROW_MAX_MS = 250


@dataclass
class OptimizerMetrics:
    silence_trim_ms: int = 0
    gap_redistributed_ms: int = 0
    tempo_change: float = 1.0
    stretch_percent: float = 0.0
    crossfade_ms: int = 0
    borrowed_time_ms: int = 0
    overflow_count: int = 0
    overlap_count: int = 0
    levels_applied: list[str] = field(default_factory=list)
    scheduler_iterations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OptimizerResult:
    ok: bool
    overflow: bool = False
    levels_applied: list[str] = field(default_factory=list)
    metrics: OptimizerMetrics = field(default_factory=OptimizerMetrics)
    fingerprint: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "overflow": self.overflow,
            "levels_applied": list(self.levels_applied),
            "metrics": self.metrics.to_dict(),
            "fingerprint": self.fingerprint,
            "detail": self.detail,
        }


def _slot_ms(seg: dict[str, Any]) -> int:
    start = int(seg.get("start_ms") or seg.get("start_time_ms") or 0)
    end = int(seg.get("end_ms") or seg.get("end_time_ms") or 0)
    if end > start:
        return max(1, end - start)
    explicit = int(seg.get("slot_ms") or 0)
    return max(1, explicit) if explicit > 0 else 1


def _audio_ms(seg: dict[str, Any], resolve_path: Callable[[str], Path] | None = None) -> int:
    for key in ("playback_duration", "fitted_ms", "tts_ms", "actual_duration_ms"):
        val = seg.get(key)
        if val is not None:
            try:
                ms = int(val)
                if ms > 0:
                    return ms
            except (TypeError, ValueError):
                pass
    fname = seg.get("fitted_file") or seg.get("file") or seg.get("tts_file_path")
    if fname and resolve_path is not None:
        try:
            path = resolve_path(str(fname))
            if path.is_file():
                from pydub import AudioSegment

                return len(AudioSegment.from_file(str(path)))
        except Exception:
            pass
    return 0


def deterministic_fingerprint(
    segments: list[dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
) -> str:
    """Stable hash of timing-relevant state (no wall-clock / random)."""
    payload = {
        "settings": settings or {},
        "segments": [
            {
                "segment_id": s.get("segment_id"),
                "start_ms": s.get("start_ms"),
                "end_ms": s.get("end_ms"),
                "playback_rate": s.get("playback_rate"),
                "silence_trim": s.get("silence_trim"),
                "stretch_factor": s.get("stretch_factor"),
                "place_start": s.get("place_start"),
                "overflow": s.get("overflow"),
                "translated_text": s.get("translated_text") or s.get("text"),
            }
            for s in segments
            if isinstance(s, dict)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AudioTimingOptimizer:
    """Audio-only timing ladder. Never mutates locked text fields."""

    def __init__(
        self,
        *,
        tempo_min: float = TEMPO_MIN,
        tempo_max: float = TEMPO_MAX,
        crossfade_ms: int = CROSSFADE_MS_DEFAULT,
        borrow_max_ms: int = BORROW_MAX_MS,
        work_dir: Path | None = None,
        resolve_path: Callable[[str], Path] | None = None,
    ) -> None:
        self.tempo_min = tempo_min
        self.tempo_max = tempo_max
        self.crossfade_ms = crossfade_ms
        self.borrow_max_ms = borrow_max_ms
        self.work_dir = work_dir
        self.resolve_path = resolve_path

    def optimize_project(
        self,
        segments: list[dict[str, Any]],
        *,
        info: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> OptimizerResult:
        from engines.pipeline_integrity.translation_lock import (
            LOCKED_TEXT_FIELDS,
            is_segment_locked,
        )
        from engines.scheduler import get_scheduler

        # Snapshot locked text for determinism / regression guard
        text_before = {
            str(s.get("segment_id") or i): {
                k: s.get(k) for k in LOCKED_TEXT_FIELDS if k in s
            }
            for i, s in enumerate(segments)
            if isinstance(s, dict)
        }

        metrics = OptimizerMetrics()
        levels: list[str] = []
        sched = get_scheduler(info)
        overflow_total = 0
        overlap_total = 0

        # Level 1–6 per segment, then project-level overlap pass
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") is not None or seg.get("merged_into_id"):
                continue
            sid = str(seg.get("segment_id") or "")
            if not sid:
                continue

            slot = _slot_ms(seg)
            audio = _audio_ms(seg, self.resolve_path)
            if audio <= 0:
                continue

            # 1. Trim Silence (metadata / flag — actual DSP in timing_fit when file present)
            trim = int(seg.get("silence_trim") or 0)
            if audio > slot and trim <= 0:
                estimated_trim = min(int(audio * 0.05), max(0, audio - slot), 80)
                if estimated_trim > 0:
                    sched.update_time(
                        segments,
                        sid,
                        silence_trim=float(estimated_trim),
                    )
                    metrics.silence_trim_ms += estimated_trim
                    audio = max(1, audio - estimated_trim)
                    if "trim_silence" not in levels:
                        levels.append("trim_silence")
                    from engines.dub_engine_v2.adaptation_decision import (
                        mark_adaptation_executed,
                    )

                    mark_adaptation_executed(
                        seg, decision="trim_silence", stages=["trim_silence"]
                    )
                    seg.setdefault("ato_levels", []).append("trim_silence")

            # 2. Redistribute Gap — extend end into free gap after segment
            if audio > slot and i + 1 < len(segments):
                nxt = segments[i + 1]
                if isinstance(nxt, dict):
                    gap = int(nxt.get("start_ms") or 0) - int(seg.get("end_ms") or 0)
                    if gap > 0:
                        take = min(gap, audio - slot, 400)
                        if take > 0:
                            new_end = int(seg.get("end_ms") or 0) + take
                            sched.update_time(segments, sid, end_ms=new_end)
                            metrics.gap_redistributed_ms += take
                            slot = _slot_ms(seg)
                            if "redistribute_gap" not in levels:
                                levels.append("redistribute_gap")

            # 3. Tempo ±5%; emergency ±12% if overflow still >15%
            if audio > slot:
                needed = audio / max(slot, 1)
                overflow_pct = (audio - slot) / max(slot, 1)
                cap = self.tempo_max
                if needed > self.tempo_max and overflow_pct > 0.15:
                    cap = max(self.tempo_max, TEMPO_EMERGENCY_MAX)
                rate = min(cap, max(1.0, needed))
                if rate > 1.0 and rate <= cap:
                    sched.update_time(segments, sid, playback_rate=rate)
                    metrics.tempo_change = max(metrics.tempo_change, rate)
                    audio = int(audio / rate)
                    tag = "tempo_emergency" if cap > self.tempo_max + 0.001 else "tempo"
                    if tag not in levels:
                        levels.append(tag)

            # 4. Micro Stretch (±5%)
            if audio > slot:
                stretch = min(MICRO_STRETCH_MAX, audio / max(slot, 1))
                if stretch > 1.0:
                    pct = (stretch - 1.0) * 100.0
                    sched.update_time(
                        segments,
                        sid,
                        stretch_factor=stretch,
                        playback_rate=float(seg.get("playback_rate") or 1.0),
                    )
                    metrics.stretch_percent = max(metrics.stretch_percent, pct)
                    audio = int(audio / stretch)
                    if "micro_stretch" not in levels:
                        levels.append("micro_stretch")

            # 5. Crossfade (metadata — mixer applies)
            if audio > slot and self.crossfade_ms > 0:
                cf = min(self.crossfade_ms, max(0, audio - slot))
                if cf > 0:
                    meta = dict(seg.get("timing_meta") or {})
                    meta["crossfade_ms"] = cf
                    sched.update_time(segments, sid, timing_meta=meta)
                    metrics.crossfade_ms = max(metrics.crossfade_ms, cf)
                    audio = max(slot, audio - cf)
                    if "crossfade" not in levels:
                        levels.append("crossfade")

            # 6. Borrow Time from next gap / neighbor slack
            if audio > slot and i + 1 < len(segments):
                nxt = segments[i + 1]
                if isinstance(nxt, dict) and nxt.get("segment_id"):
                    nxt_sid = str(nxt["segment_id"])
                    nxt_slot = _slot_ms(nxt)
                    nxt_audio = _audio_ms(nxt, self.resolve_path) or nxt_slot
                    slack = max(0, nxt_slot - nxt_audio)
                    borrow = min(self.borrow_max_ms, audio - slot, slack)
                    if borrow > 0:
                        sched.update_time(
                            segments,
                            sid,
                            end_ms=int(seg.get("end_ms") or 0) + borrow,
                        )
                        sched.update_time(
                            segments,
                            nxt_sid,
                            start_ms=int(nxt.get("start_ms") or 0) + borrow,
                        )
                        metrics.borrowed_time_ms += borrow
                        slot = _slot_ms(seg)
                        audio = _audio_ms(seg, self.resolve_path) or audio
                        if "borrow_time" not in levels:
                            levels.append("borrow_time")

            # 7. Overflow — pipeline state via Overflow Manager (never text)
            if audio > slot + 25:
                overflow_total += 1
                ov = audio - slot
                sched.update_time(
                    segments,
                    sid,
                    overflow=True,
                    slot_overflow=True,
                    overflow_ms=ov,
                    overflow_pct=round(100.0 * ov / max(slot, 1), 1),
                )
                try:
                    from engines.pipeline_integrity.overflow_manager import (
                        register_overflow,
                    )

                    register_overflow(
                        seg,
                        index=i,
                        overflow_ms=ov,
                        slot_ms=slot,
                        reason="ato_level_overflow",
                    )
                except Exception:
                    pass
                if "overflow" not in levels:
                    levels.append("overflow")

            # 8. Underflow — padding plan only (never text expand)
            if audio > 0 and slot > audio + 25:
                try:
                    from engines.pipeline_integrity.underflow_manager import (
                        register_underflow,
                    )

                    register_underflow(
                        seg,
                        index=i,
                        shortfall_ms=slot - audio,
                        slot_ms=slot,
                        audio_ms=audio,
                        reason="ato_level_underflow",
                    )
                    if "underflow" not in levels:
                        levels.append("underflow")
                except Exception:
                    pass
        # Project overlap pass — shift via Scheduler only
        for i in range(len(segments) - 1):
            a = segments[i]
            b = segments[i + 1]
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            if a.get("merged_into") or b.get("merged_into"):
                continue
            a_end = int(a.get("end_ms") or 0)
            b_start = int(b.get("start_ms") or 0)
            if a_end > b_start:
                overlap_total += 1
                sid_b = str(b.get("segment_id") or "")
                if sid_b:
                    # Push B start to clear overlap (deterministic: always forward)
                    sched.update_time(segments, sid_b, start_ms=a_end)
                    if "overlap_resolve" not in levels:
                        levels.append("overlap_resolve")

        metrics.overflow_count = overflow_total
        metrics.overlap_count = overlap_total
        metrics.levels_applied = list(levels)
        metrics.scheduler_iterations = sched.iterations

        # Guard: locked text unchanged
        for i, s in enumerate(segments):
            if not isinstance(s, dict):
                continue
            if not is_segment_locked(s):
                continue
            sid = str(s.get("segment_id") or i)
            before = text_before.get(sid) or {}
            for k, v in before.items():
                if s.get(k) != v:
                    raise RuntimeError(
                        f"AudioTimingOptimizer mutated locked text field {k!r} "
                        f"on segment {sid}"
                    )

        fp = deterministic_fingerprint(segments, settings=settings)
        if info is not None:
            info["audio_timing_optimizer"] = {
                "metrics": metrics.to_dict(),
                "fingerprint": fp,
                "levels_applied": levels,
            }

        return OptimizerResult(
            ok=overflow_total == 0,
            overflow=overflow_total > 0,
            levels_applied=levels,
            metrics=metrics,
            fingerprint=fp,
            detail=f"overflow={overflow_total} overlap_fixed={overlap_total}",
        )


def optimize_audio_timing(
    segments: list[dict[str, Any]],
    *,
    info: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    **kwargs: Any,
) -> OptimizerResult:
    return AudioTimingOptimizer(**kwargs).optimize_project(
        segments, info=info, settings=settings
    )
