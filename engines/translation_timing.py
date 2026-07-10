"""Translation stage timing breakdown — Marian vs LLM vs post-processing."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

SUBPHASE_TO_BUCKET: dict[str, str] = {
    "marian_mt": "marian_mt",
    "post_mt_restore": "marian_mt",
    "naturalizer_rules": "llm_adaptation",
    "llm_adaptation": "llm_adaptation",
    "validation": "post_processing",
    "post_processing": "post_processing",
    "done": "",
}

UI_BUCKET_KEYS = ("marian_mt", "llm_adaptation", "post_processing")


def format_duration_clock(sec: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    s = max(0, int(round(float(sec or 0))))
    h, rem = divmod(s, 3600)
    m, r = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{r:02d}"
    return f"{m}:{r:02d}"


def format_duration_hms(sec: float) -> str:
    """Format seconds as HH:MM:SS (UI)."""
    s = max(0, int(round(float(sec or 0))))
    h, rem = divmod(s, 3600)
    m, r = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{r:02d}"


def format_duration_verbose(sec: float, *, lang: str = "ru") -> str:
    s = max(0, int(round(float(sec or 0))))
    m, r = divmod(s, 60)
    if lang == "en":
        if m:
            return f"{m} min {r} sec"
        return f"{r} sec"
    if m:
        return f"{m} мин {r} сек"
    return f"{r} сек"


@dataclass
class TranslationTimingBreakdown:
    """Sub-timers inside the «Перевод» pipeline step."""

    marian_sec: float = 0.0
    llm_adaptation_sec: float = 0.0
    validation_sec: float = 0.0
    post_processing_sec: float = 0.0
    timing_aware_sec: float = 0.0
    restore_sec: float = 0.0
    naturalizer_rules_sec: float = 0.0
    current_subphase: str = ""
    llm_model: str = ""
    llm_provider: str = ""
    segment_count: int = 0
    marian_segments_done: int = 0
    llm_segments_done: int = 0

    @property
    def translation_total_sec(self) -> float:
        return (
            self.marian_sec
            + self.llm_adaptation_sec
            + self.validation_sec
            + self.post_processing_sec
            + self.timing_aware_sec
            + self.restore_sec
            + self.naturalizer_rules_sec
        )

    def ui_buckets(self) -> dict[str, float]:
        """Three UI bars under «Перевод»."""
        post = (
            self.validation_sec
            + self.post_processing_sec
            + self.restore_sec
            + self.naturalizer_rules_sec
            + self.timing_aware_sec
        )
        return {
            "marian_mt": round(self.marian_sec, 2),
            "llm_adaptation": round(self.llm_adaptation_sec, 2),
            "post_processing": round(post, 2),
        }

    def segment_stats(self) -> dict[str, dict[str, Any]]:
        n = max(1, int(self.segment_count or 0))
        buckets = self.ui_buckets()
        out: dict[str, dict[str, Any]] = {}
        for key, sec in buckets.items():
            seg_n = n
            if key == "marian_mt" and self.marian_segments_done:
                seg_n = self.marian_segments_done
            elif key == "llm_adaptation" and self.llm_segments_done:
                seg_n = self.llm_segments_done
            out[key] = {
                "segments": seg_n,
                "sec": round(float(sec), 1),
                "avg_sec_per_segment": round(float(sec) / max(seg_n, 1), 3),
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        buckets = self.ui_buckets()
        total = sum(buckets.values())
        stats = self.segment_stats()
        phase_status = {key: "done" for key in UI_BUCKET_KEYS}
        return {
            "marian_sec": round(self.marian_sec, 3),
            "llm_adaptation_sec": round(self.llm_adaptation_sec, 3),
            "validation_sec": round(self.validation_sec, 3),
            "post_processing_sec": round(self.post_processing_sec, 3),
            "timing_aware_sec": round(self.timing_aware_sec, 3),
            "restore_sec": round(self.restore_sec, 3),
            "naturalizer_rules_sec": round(self.naturalizer_rules_sec, 3),
            "translation_total_sec": round(total, 3),
            "segment_count": int(self.segment_count or 0),
            "marian_segments_done": int(self.marian_segments_done or 0),
            "llm_segments_done": int(self.llm_segments_done or 0),
            "current_subphase": self.current_subphase,
            "llm_model": self.llm_model,
            "llm_provider": self.llm_provider,
            "ui_buckets": buckets,
            "segment_stats": stats,
            "phase_status": phase_status,
            "ui_labels": {
                "marian_mt": "Marian MT",
                "llm_adaptation": "Qwen / LLM Adaptation",
                "post_processing": "Post-processing",
            },
        }


def build_breakdown(
    *,
    marian_sec: float = 0.0,
    naturalizer_sec: float = 0.0,
    llm_ms_total: float = 0.0,
    restore_sec: float = 0.0,
    validation_sec: float = 0.0,
    semantic_sec: float = 0.0,
    timing_aware_sec: float = 0.0,
    timing_aware_llm_sec: float = 0.0,
    post_gate_sec: float = 0.0,
    llm_model: str = "",
    llm_provider: str = "",
    segment_count: int = 0,
    marian_segments_done: int = 0,
    llm_segments_done: int = 0,
) -> TranslationTimingBreakdown:
    llm_sec = max(0.0, float(llm_ms_total) / 1000.0) + max(0.0, timing_aware_llm_sec)
    rules_sec = max(0.0, float(naturalizer_sec) - max(0.0, float(llm_ms_total) / 1000.0))
    return TranslationTimingBreakdown(
        marian_sec=max(0.0, marian_sec),
        llm_adaptation_sec=llm_sec,
        validation_sec=max(0.0, validation_sec) + max(0.0, semantic_sec),
        post_processing_sec=max(0.0, post_gate_sec),
        timing_aware_sec=max(0.0, timing_aware_sec),
        restore_sec=max(0.0, restore_sec),
        naturalizer_rules_sec=rules_sec,
        llm_model=llm_model or "",
        llm_provider=llm_provider or "",
        segment_count=max(0, int(segment_count or 0)),
        marian_segments_done=max(0, int(marian_segments_done or 0)),
        llm_segments_done=max(0, int(llm_segments_done or 0)),
    )


def _bucket_phase_status(
    bucket: str,
    *,
    current_subphase: str,
    frozen: dict[str, dict[str, Any]],
) -> str:
    if frozen.get(bucket):
        return "done"
    cur_bucket = SUBPHASE_TO_BUCKET.get(current_subphase, "")
    if cur_bucket == bucket:
        return "active"
    bucket_order = list(UI_BUCKET_KEYS)
    cur_idx = bucket_order.index(cur_bucket) if cur_bucket in bucket_order else -1
    idx = bucket_order.index(bucket)
    if cur_idx >= 0 and idx < cur_idx:
        return "done"
    return "pending"


def init_live_timing(task_id: str, *, segment_count: int) -> None:
    if not task_id:
        return
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        now = time.perf_counter()
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if not task:
                return
            info = task.setdefault("info", {})
            info["translation_live_timing"] = {
                "segment_count": max(0, int(segment_count)),
                "translate_started_mono": now,
                "phase_started_mono": now,
                "current_subphase": "marian_mt",
                "frozen": {},
                "marian_segments_done": 0,
                "llm_segments_done": 0,
            }
    except Exception:
        pass


def _compute_live_buckets(tracker: dict[str, Any]) -> dict[str, float]:
    buckets = {k: 0.0 for k in UI_BUCKET_KEYS}
    frozen = tracker.get("frozen") or {}
    for key in UI_BUCKET_KEYS:
        row = frozen.get(key)
        if row:
            buckets[key] = float(row.get("sec") or 0)
    cur = str(tracker.get("current_subphase") or "")
    bucket = SUBPHASE_TO_BUCKET.get(cur, "")
    if bucket and not frozen.get(bucket):
        phase_start = float(tracker.get("phase_started_mono") or tracker.get("translate_started_mono") or 0)
        if phase_start:
            buckets[bucket] = max(0.0, time.perf_counter() - phase_start)
    return {k: round(v, 2) for k, v in buckets.items()}


def _freeze_current_phase(
    tracker: dict[str, Any],
    *,
    elapsed_sec: float | None = None,
    segments_done: int | None = None,
) -> None:
    cur = str(tracker.get("current_subphase") or "")
    bucket = SUBPHASE_TO_BUCKET.get(cur, "")
    if not bucket:
        return
    frozen = tracker.setdefault("frozen", {})
    if frozen.get(bucket):
        return
    phase_start = float(tracker.get("phase_started_mono") or tracker.get("translate_started_mono") or 0)
    sec = float(elapsed_sec) if elapsed_sec is not None else max(0.0, time.perf_counter() - phase_start)
    seg = segments_done
    if seg is None:
        if bucket == "marian_mt":
            seg = int(tracker.get("marian_segments_done") or tracker.get("segment_count") or 0)
        elif bucket == "llm_adaptation":
            seg = int(tracker.get("llm_segments_done") or tracker.get("segment_count") or 0)
        else:
            seg = int(tracker.get("segment_count") or 0)
    frozen[bucket] = {"sec": round(sec, 2), "segments": int(seg or 0)}


def _build_live_timing_payload(tracker: dict[str, Any], subphase: str) -> dict[str, Any]:
    segment_count = int(tracker.get("segment_count") or 0)
    buckets = _compute_live_buckets(tracker)
    frozen = tracker.get("frozen") or {}
    phase_status = {
        key: _bucket_phase_status(key, current_subphase=subphase, frozen=frozen)
        for key in UI_BUCKET_KEYS
    }
    segment_stats: dict[str, dict[str, Any]] = {}
    for key, sec in buckets.items():
        row = frozen.get(key) or {}
        seg_n = int(row.get("segments") or 0)
        if key == "marian_mt" and not seg_n:
            seg_n = int(tracker.get("marian_segments_done") or 0)
        if key == "llm_adaptation" and not seg_n:
            seg_n = int(tracker.get("llm_segments_done") or 0)
        if not seg_n and phase_status[key] == "done":
            seg_n = segment_count
        segment_stats[key] = {
            "segments": seg_n,
            "sec": round(float(sec), 1),
            "avg_sec_per_segment": round(float(sec) / max(seg_n or segment_count or 1, 1), 3),
            "status": phase_status[key],
        }
    return {
        "marian_sec": buckets["marian_mt"],
        "llm_adaptation_sec": buckets["llm_adaptation"],
        "validation_sec": 0.0,
        "post_processing_sec": buckets["post_processing"],
        "translation_total_sec": round(sum(buckets.values()), 2),
        "segment_count": segment_count,
        "marian_segments_done": int(tracker.get("marian_segments_done") or 0),
        "llm_segments_done": int(tracker.get("llm_segments_done") or 0),
        "current_subphase": subphase,
        "ui_buckets": buckets,
        "phase_status": phase_status,
        "segment_stats": segment_stats,
        "ui_labels": {
            "marian_mt": "Marian MT",
            "llm_adaptation": "Qwen / LLM Adaptation",
            "post_processing": "Post-processing",
        },
    }


def push_live_subphase(
    task_id: str,
    subphase: str,
    *,
    breakdown: TranslationTimingBreakdown | None = None,
    elapsed_sec: float | None = None,
    segments_done: int | None = None,
    segments_total: int | None = None,
) -> None:
    """Update task progress_detail with translation sub-phase (live UI)."""
    if not task_id:
        return
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        payload: dict[str, Any] = {
            "translation_subphase": subphase,
            "phase": "translate",
        }
        if breakdown is not None:
            payload["translation_timing"] = breakdown.to_dict()
        if elapsed_sec is not None:
            payload["translation_subphase_elapsed_sec"] = round(elapsed_sec, 1)

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if not task:
                return
            info = task.setdefault("info", {})
            tracker = dict(info.get("translation_live_timing") or {})
            if segments_total is not None:
                tracker["segment_count"] = max(0, int(segments_total))
            if segments_done is not None:
                bucket = SUBPHASE_TO_BUCKET.get(subphase, "")
                if bucket == "marian_mt":
                    tracker["marian_segments_done"] = max(0, int(segments_done))
                elif bucket == "llm_adaptation":
                    tracker["llm_segments_done"] = max(0, int(segments_done))

            prev = str(tracker.get("current_subphase") or "")
            prev_bucket = SUBPHASE_TO_BUCKET.get(prev, "")
            new_bucket = SUBPHASE_TO_BUCKET.get(subphase, "")
            frozen = tracker.setdefault("frozen", {})

            if subphase == "post_mt_restore" and elapsed_sec is not None:
                frozen["marian_mt"] = {
                    "sec": round(float(elapsed_sec), 2),
                    "segments": int(
                        segments_done
                        if segments_done is not None
                        else tracker.get("marian_segments_done")
                        or tracker.get("segment_count")
                        or 0
                    ),
                }

            if (
                prev_bucket
                and new_bucket
                and prev_bucket != new_bucket
                and not frozen.get(prev_bucket)
            ):
                tracker["current_subphase"] = prev
                _freeze_current_phase(
                    tracker,
                    elapsed_sec=elapsed_sec,
                    segments_done=segments_done,
                )
                tracker["phase_started_mono"] = time.perf_counter()
            elif prev and prev != subphase and prev_bucket != new_bucket:
                tracker["phase_started_mono"] = time.perf_counter()

            tracker["current_subphase"] = subphase
            if subphase == "done" and breakdown is not None:
                d = breakdown.to_dict()
                payload["translation_timing"] = d
                info["translation_live_timing"] = tracker
            elif breakdown is None:
                payload["translation_timing"] = _build_live_timing_payload(tracker, subphase)
                info["translation_live_timing"] = tracker

            info["translation_timing"] = payload.get("translation_timing") or info.get(
                "translation_timing"
            )
            detail = info.setdefault("progress_detail", {})
            detail.update(payload)
            if segments_done is not None:
                detail["segments_done"] = segments_done
            if segments_total is not None:
                detail["total_segments"] = segments_total
    except Exception:
        pass


def log_pipeline_timing_summary(
    app_dir,
    task_id: str,
    *,
    whisper_sec: float = 0.0,
    breakdown: TranslationTimingBreakdown | None = None,
    validation_sec: float | None = None,
    tts_sec: float = 0.0,
    extra: dict[str, float] | None = None,
) -> None:
    """Append human-readable timing block (TZ: separate Marian / Qwen / Validation)."""
    from engines.translation_stage_log import log_timing_summary

    br = breakdown or TranslationTimingBreakdown()
    val = validation_sec if validation_sec is not None else br.validation_sec
    lines = {
        "whisper": whisper_sec,
        "marian": br.marian_sec,
        "llm_adaptation": br.llm_adaptation_sec,
        "validation": val,
        "tts": tts_sec,
    }
    if extra:
        lines.update(extra)
    log_timing_summary(app_dir, task_id, lines)


def log_translation_debug_breakdown(
    app_dir,
    task_id: str,
    timing: dict[str, Any],
) -> None:
    """Debug Mode: Marian/Qwen/Post with segment counts and averages."""
    try:
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        if not IS_DEBUG_LEARNING_MODE():
            return
    except Exception:
        return
    from engines.translation_stage_log import log_debug_timing_breakdown

    log_debug_timing_breakdown(app_dir, task_id, timing)
