"""
Segment timing QA — sentence boundaries, overlap/pause joints, post-TTS control,
final dub QA report, and OpenDDF per-segment diagnostics (TZ §8–§11, §13).
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.segment_timing_qa")

OVERLAP_TOLERANCE_MS = 40
LONG_PAUSE_MS = 700
NATURAL_PAUSE_MS = 100
POST_TTS_OVERFLOW_RATIO = 1.12
POST_TTS_UNDERFLOW_RATIO = 0.80
MAX_POST_TTS_RETRIES = 5
DURATION_TOLERANCE_MS = 100
# Semantic Timing Adaptation (TZ): expand when dubbed speech ends ≥450ms before slot end.
SPEECH_UNDERFLOW_EXPAND_MS = 450
# KPI: 95% segments within ±275ms of original speech window.
DURATION_MATCH_GOAL_MS = 275
MAX_SEMANTIC_TIMING_ITERATIONS = 5
MAX_TEXT_ADAPTATION_ITERATIONS = MAX_SEMANTIC_TIMING_ITERATIONS
ADAPTATION_STAGES = ("auto", "moderate", "strong", "strong", "strong")

_MERGED_WORD_RE = re.compile(
    r"\b(?:вереска|нельзябыло|можнобыло|нужнобыло|"
    r"couldnot|didnot|willnot|cannothelp)\b",
    re.IGNORECASE,
)


def _parse_timing(entry) -> tuple[int, int]:
    from engines.timing_fit import _parse_timing as _pf

    return _pf(entry)


def _write_timing(entry, start_ms: int, end_ms: int):
    if isinstance(entry, dict):
        entry["start"] = int(start_ms)
        entry["end"] = int(end_ms)
    elif isinstance(entry, list) and len(entry) >= 2:
        entry[0] = int(start_ms)
        entry[1] = int(end_ms)
    return entry


def segment_playback_ms(seg: dict) -> int:
    for key in ("playback_duration", "tts_ms", "fitted_ms"):
        val = seg.get(key)
        if val is not None:
            try:
                return max(0, int(val))
            except (TypeError, ValueError):
                pass
    return 0


def detect_timing_overlaps(
    timing_map: list,
    *,
    tolerance_ms: int = OVERLAP_TOLERANCE_MS,
) -> list[dict[str, Any]]:
    """Detect overlapping adjacent slots in timing_map (TZ §9)."""
    issues: list[dict[str, Any]] = []
    if not timing_map or len(timing_map) < 2:
        return issues
    for i in range(len(timing_map) - 1):
        _, end_i = _parse_timing(timing_map[i])
        start_j, _ = _parse_timing(timing_map[i + 1])
        overlap = end_i - start_j
        if overlap > tolerance_ms:
            issues.append(
                {
                    "index": i,
                    "code": "overlap",
                    "overlap_ms": overlap,
                    "prev_end_ms": end_i,
                    "next_start_ms": start_j,
                }
            )
    return issues


def detect_long_pauses(
    timing_map: list,
    *,
    max_pause_ms: int = LONG_PAUSE_MS,
) -> list[dict[str, Any]]:
    """Detect excessive gaps between adjacent slots (TZ §9)."""
    issues: list[dict[str, Any]] = []
    if not timing_map or len(timing_map) < 2:
        return issues
    for i in range(len(timing_map) - 1):
        _, end_i = _parse_timing(timing_map[i])
        start_j, _ = _parse_timing(timing_map[i + 1])
        gap = start_j - end_i
        if gap > max_pause_ms:
            issues.append(
                {
                    "index": i,
                    "code": "long_pause",
                    "gap_ms": gap,
                    "prev_end_ms": end_i,
                    "next_start_ms": start_j,
                }
            )
    return issues


def clamp_timeline_to_video_duration(
    segments_data: list[dict] | None,
    timing_map: list | None,
    video_duration_ms: int,
    *,
    min_slot_ms: int = 800,
    min_gap_ms: int = 40,
) -> list[dict[str, Any]]:
    """Keep every segment inside [0, video_duration_ms].

    Speech-expanded splits can push later children past the mux ``-t`` cut
    (video length). Those lines are never heard — «дубляж не доходит до конца».

    Strategy:
      1) shrink long inter-segment gaps (largest first);
      2) if still overshooting, scale the whole timeline into the video;
      3) hard-cap last end and pull any start that still lands at/after video.
    Mutates ``segments_data`` / ``timing_map`` in place. Returns fix log.
    """
    video_ms = int(video_duration_ms or 0)
    fixes: list[dict[str, Any]] = []
    if video_ms <= min_slot_ms:
        return fixes
    if not timing_map:
        return fixes

    def _bounds(i: int) -> tuple[int, int]:
        if segments_data is not None and i < len(segments_data):
            seg = segments_data[i]
            if seg.get("start_ms") is not None and seg.get("end_ms") is not None:
                return int(seg["start_ms"]), int(seg["end_ms"])
        if i < len(timing_map):
            return _parse_timing(timing_map[i])
        return 0, 0

    def _write(i: int, start_ms: int, end_ms: int) -> None:
        start_ms = max(0, int(start_ms))
        end_ms = max(start_ms + 1, int(end_ms))
        if i < len(timing_map):
            _write_timing(timing_map[i], start_ms, end_ms)
        if segments_data is not None and i < len(segments_data):
            seg = segments_data[i]
            seg["start_ms"] = start_ms
            seg["end_ms"] = end_ms
            seg["slot_ms"] = max(1, end_ms - start_ms)

    n = min(
        len(timing_map),
        len(segments_data) if segments_data is not None else len(timing_map),
    )
    if n <= 0:
        return fixes

    ends = [_bounds(i)[1] for i in range(n)]
    max_end = max(ends) if ends else 0
    if max_end <= video_ms:
        # Still pull any stray start that sits at/after video (empty tail).
        for i in range(n):
            st, en = _bounds(i)
            if st >= video_ms:
                new_st = max(0, video_ms - min_slot_ms)
                new_en = video_ms
                _write(i, new_st, new_en)
                fixes.append(
                    {
                        "index": i,
                        "action": "pull_start_into_video",
                        "old_start_ms": st,
                        "old_end_ms": en,
                        "new_start_ms": new_st,
                        "new_end_ms": new_en,
                    }
                )
        return fixes

    overshoot = max_end - video_ms
    fixes.append(
        {
            "index": -1,
            "action": "overshoot_detected",
            "max_end_ms": max_end,
            "video_ms": video_ms,
            "overshoot_ms": overshoot,
        }
    )

    # 1) Absorb overshoot from gaps (keep relative speech order).
    gap_idxs = list(range(n - 1))
    gap_idxs.sort(
        key=lambda i: max(0, _bounds(i + 1)[0] - _bounds(i)[1]),
        reverse=True,
    )
    for i in gap_idxs:
        if overshoot <= 0:
            break
        st_i, en_i = _bounds(i)
        st_j, en_j = _bounds(i + 1)
        gap = st_j - en_i
        reducible = gap - min_gap_ms
        if reducible <= 0:
            continue
        take = min(reducible, overshoot)
        # Shift segment i+1 … n-1 left by ``take``.
        for k in range(i + 1, n):
            st_k, en_k = _bounds(k)
            _write(k, st_k - take, en_k - take)
        overshoot -= take
        fixes.append(
            {
                "index": i,
                "action": "shrink_gap",
                "reduced_ms": take,
                "remaining_overshoot_ms": overshoot,
            }
        )

    ends = [_bounds(i)[1] for i in range(n)]
    max_end = max(ends) if ends else 0

    # 2) Scale whole timeline into the video window.
    if max_end > video_ms and max_end > 0:
        scale = float(video_ms) / float(max_end)
        for i in range(n):
            st, en = _bounds(i)
            new_st = int(round(st * scale))
            new_en = int(round(en * scale))
            if new_en - new_st < min_slot_ms and en > st:
                new_en = min(video_ms, new_st + min_slot_ms)
            _write(i, new_st, min(video_ms, new_en))
        fixes.append(
            {
                "index": -1,
                "action": "scale_to_video",
                "scale": round(scale, 6),
                "old_max_end_ms": max_end,
                "new_max_end_ms": video_ms,
            }
        )

    # 3) Hard cap + pull starts that still sit at/after video.
    for i in range(n):
        st, en = _bounds(i)
        if en > video_ms or st >= video_ms:
            new_st = st if st < video_ms else max(0, video_ms - min_slot_ms)
            new_en = video_ms
            if new_en - new_st < 1:
                new_st = max(0, video_ms - min_slot_ms)
            _write(i, new_st, new_en)
            fixes.append(
                {
                    "index": i,
                    "action": "hard_cap_video",
                    "old_start_ms": st,
                    "old_end_ms": en,
                    "new_start_ms": new_st,
                    "new_end_ms": new_en,
                }
            )

    # Keep monotonic non-overlapping order after edits.
    cursor = 0
    for i in range(n):
        st, en = _bounds(i)
        if st < cursor:
            delta = cursor - st
            st += delta
            en = max(st + 1, en + delta)
        if en > video_ms:
            en = video_ms
            st = min(st, max(0, en - min_slot_ms))
        if en <= st:
            en = min(video_ms, st + max(1, min_slot_ms // 4))
            st = max(0, en - max(1, min_slot_ms // 4))
        _write(i, st, en)
        cursor = en + min_gap_ms

    return fixes


def normalize_timing_map_joints(
    timing_map: list,
    *,
    max_pause_ms: int = LONG_PAUSE_MS,
    natural_pause_ms: int = NATURAL_PAUSE_MS,
    tolerance_ms: int = OVERLAP_TOLERANCE_MS,
) -> tuple[list, list[dict[str, Any]]]:
    """
    Auto-fix overlaps and long pauses in timing_map (TZ §9).
    Returns (normalized_map, fix_log).
    """
    if not timing_map:
        return timing_map, []
    normalized = copy.deepcopy(timing_map)
    fixes: list[dict[str, Any]] = []

    for i in range(len(normalized) - 1):
        start_i, end_i = _parse_timing(normalized[i])
        start_j, end_j = _parse_timing(normalized[i + 1])
        overlap = end_i - start_j
        if overlap > tolerance_ms:
            new_end_i = max(start_i + 80, start_j - natural_pause_ms)
            _write_timing(normalized[i], start_i, new_end_i)
            fixes.append(
                {
                    "index": i,
                    "action": "trim_overlap",
                    "overlap_ms": overlap,
                    "new_end_ms": new_end_i,
                }
            )
            end_i = new_end_i

        start_j, end_j = _parse_timing(normalized[i + 1])
        gap = start_j - end_i
        if gap > max_pause_ms:
            new_start_j = end_i + natural_pause_ms
            if new_start_j < end_j:
                _write_timing(normalized[i + 1], new_start_j, end_j)
                fixes.append(
                    {
                        "index": i,
                        "action": "reduce_pause",
                        "gap_ms": gap,
                        "new_start_ms": new_start_j,
                    }
                )

    return normalized, fixes


def segment_duration_fits(
    tts_ms: int,
    slot_ms: int,
    *,
    tolerance_ms: int = DURATION_TOLERANCE_MS,
) -> bool:
    """TTS fits source segment slot within ±tolerance_ms (TZ stage 2)."""
    return int(tts_ms) <= int(slot_ms) + int(tolerance_ms)


def segment_duration_matches_goal(
    tts_ms: int,
    slot_ms: int,
    *,
    goal_ms: int = DURATION_MATCH_GOAL_MS,
) -> bool:
    """Bidirectional duration match — dubbed speech ends near original slot end (TZ KPI)."""
    if slot_ms <= 0 or tts_ms <= 0:
        return False
    return abs(int(tts_ms) - int(slot_ms)) <= int(goal_ms)


def speech_duration_match_score(
    tts_ms: int,
    slot_ms: int,
    *,
    goal_ms: int = DURATION_MATCH_GOAL_MS,
) -> int:
    """0–100 score: 100 when within goal, lower as |tts−slot| grows."""
    if slot_ms <= 0 or tts_ms <= 0:
        return 0
    diff = abs(int(tts_ms) - int(slot_ms))
    if diff <= goal_ms:
        return 100
    overshoot = diff - goal_ms
    penalty = min(100, round(overshoot / max(int(slot_ms), 1) * 400))
    return max(0, 100 - penalty)


def speech_ends_early(
    tts_ms: int,
    slot_ms: int,
    *,
    min_gap_ms: int = SPEECH_UNDERFLOW_EXPAND_MS,
) -> bool:
    """True when dubbed speech finishes noticeably before the original slot ends."""
    if slot_ms <= 0 or tts_ms <= 0:
        return False
    return int(slot_ms) - int(tts_ms) >= int(min_gap_ms)


def detect_post_tts_deviations(
    seg: dict,
    idx: int,
    timing_map: list,
    *,
    overflow_ratio: float = POST_TTS_OVERFLOW_RATIO,
    underflow_ratio: float = POST_TTS_UNDERFLOW_RATIO,
) -> list[dict[str, Any]]:
    """Per-segment post-TTS checks: duration, overlap with next, long pause (TZ §10)."""
    issues: list[dict[str, Any]] = []
    if seg.get("merged_into") is not None:
        return issues
    if not (seg.get("file") or seg.get("tts_file_path")):
        return issues

    start_ms, end_ms = (
        _parse_timing(timing_map[idx]) if idx < len(timing_map) else (0, 3000)
    )
    slot_ms = max(1, end_ms - start_ms)
    playback_ms = segment_playback_ms(seg)
    if playback_ms <= 0:
        return issues

    seg["actual_start_ms"] = start_ms
    seg["actual_end_ms"] = start_ms + playback_ms
    seg["slot_ms"] = slot_ms

    if not segment_duration_fits(playback_ms, slot_ms):
        issues.append(
            {
                "idx": idx,
                "code": "duration_overflow",
                "tts_ms": playback_ms,
                "slot_ms": slot_ms,
                "window_ms": slot_ms,
                "overflow_ms": max(0, playback_ms - slot_ms),
            }
        )
    elif speech_ends_early(playback_ms, slot_ms) and slot_ms > 400:
        issues.append(
            {
                "idx": idx,
                "code": "duration_underflow",
                "tts_ms": playback_ms,
                "slot_ms": slot_ms,
                "window_ms": slot_ms,
                "speech_difference_ms": slot_ms - playback_ms,
            }
        )

    actual_end = start_ms + playback_ms
    for nxt_idx in range(idx + 1, len(timing_map)):
        nxt_start, _ = _parse_timing(timing_map[nxt_idx])
        overlap = actual_end - nxt_start
        if overlap > OVERLAP_TOLERANCE_MS:
            issues.append(
                {
                    "idx": idx,
                    "code": "overlap_with_next",
                    "overlap_ms": overlap,
                    "next_index": nxt_idx,
                    "tts_ms": playback_ms,
                    "window_ms": slot_ms,
                }
            )
            break
        gap = nxt_start - actual_end
        if gap > LONG_PAUSE_MS:
            issues.append(
                {
                    "idx": idx,
                    "code": "long_pause_after",
                    "gap_ms": gap,
                    "next_index": nxt_idx,
                    "tts_ms": playback_ms,
                    "window_ms": slot_ms,
                }
            )
        break

    return issues


def _update_speech_timing_diagnostics(
    adapt_trace: dict[str, Any],
    seg: dict,
    *,
    slot_ms: int,
    start_ms: int,
) -> int:
    """Populate Semantic Timing Adaptation fields on text_adaptation_trace."""
    playback_ms = segment_playback_ms(seg)
    adapt_trace["original_speech_end_ms"] = start_ms + slot_ms
    adapt_trace["tts_speech_end_ms"] = start_ms + playback_ms
    adapt_trace["speech_difference_ms"] = slot_ms - playback_ms
    adapt_trace["duration_match_score"] = speech_duration_match_score(
        playback_ms, slot_ms
    )
    adapt_trace["expand_required"] = bool(
        adapt_trace.get("expand_required")
        or speech_ends_early(playback_ms, slot_ms)
    )
    return playback_ms


def post_tts_validate_and_retry(
    segments_data: list[dict],
    timing_map: list,
    *,
    source_segments: list[str],
    voice: str,
    target_lang: str,
    src_lang: str,
    audits: list[dict] | None = None,
    task_id: str | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    max_retries: int = MAX_TEXT_ADAPTATION_ITERATIONS,
    regen_fn=None,
    commit_fn=None,
) -> dict[str, Any]:
    """
    Post-TTS control loop (TZ §10, stage 2): measure TTS, adapt text, regen until fit.
    Text-only — no atempo / rate / time-stretch compensation.
    """
    from engines.semantic_adaptation import (
        estimate_tts_duration_ms,
        record_post_tts_adaptation,
    )
    from engines.semantic_optimizer import (
        optimize_expand_for_slot,
        optimize_llm_rephrase_for_slot,
    )

    # Continue the same per-run LLM budget started in timing-aware translation,
    # so the post-TTS retry loop cannot blow the dub time on a slow local model.
    try:
        from engines.translation_adapt import begin_llm_run

        begin_llm_run(task_id)
    except Exception:
        pass

    stats: dict[str, Any] = {
        "checked": 0,
        "deviations": 0,
        "retries": 0,
        "fixed": 0,
        "adaptation_executed": False,
        "issues": [],
    }
    audit_by_idx = {int(a.get("index", -1)): a for a in (audits or [])}

    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        if not (seg.get("file") or seg.get("tts_file_path")):
            continue
        stats["checked"] += 1

        retry_meta = seg.setdefault("post_tts_retry", {"attempts": 0, "reasons": []})
        adapt_trace = seg.setdefault(
            "text_adaptation_trace",
            {
                "executed": False,
                "iterations": 0,
                "original_duration_ms": 0,
                "first_tts_duration_ms": 0,
                "final_tts_duration_ms": 0,
                "reasons": [],
                "stages": [],
                "text_before": "",
                "text_after": "",
                "expand_required": False,
                "expand_executed": False,
                "expansion_strategy": "",
                "expansion_iterations": 0,
                "duration_match_score": 0,
            },
        )

        start_ms, end_ms = (
            _parse_timing(timing_map[idx]) if idx < len(timing_map) else (0, 3000)
        )
        slot_ms = max(1, end_ms - start_ms)
        adapt_trace["original_duration_ms"] = slot_ms
        adapt_trace["timing_source"] = "timing_map"
        if seg.get("tts_timing"):
            adapt_trace["timing_source"] = "tts_timing"
        adapt_trace["start_time_ms"] = start_ms
        adapt_trace["end_time_ms"] = end_ms

        first_ms = segment_playback_ms(seg)
        if first_ms > 0:
            adapt_trace["first_tts_duration_ms"] = first_ms

        expansion_iterations = 0
        # Anti-oscillation: once a segment has been shrunk (overflow) we must not
        # then expand the resulting slack (and vice versa). Reversing direction
        # ping-pongs the (slow) LLM between overflow↔underflow and can burn every
        # retry — the visible "hang on TTS" on CPU/local models. First adaptation
        # locks the direction; a reverse deviation is accepted as good enough.
        adapt_direction: str | None = None
        _update_speech_timing_diagnostics(
            adapt_trace, seg, slot_ms=slot_ms, start_ms=start_ms
        )

        for attempt in range(max_retries + 1):
            playback_ms = _update_speech_timing_diagnostics(
                adapt_trace, seg, slot_ms=slot_ms, start_ms=start_ms
            )
            speech_end: dict[str, Any] = {}
            try:
                from engines.tts_speech_end import apply_speech_end_to_segment

                speech_end = apply_speech_end_to_segment(
                    seg,
                    wav_path=seg.get("tts_file_path") or seg.get("file"),
                    slot_ms=slot_ms,
                )
                adapt_trace["voice_truncated"] = bool(speech_end.get("voice_truncated"))
                adapt_trace["voice_finished_naturally"] = bool(
                    speech_end.get("voice_finished_naturally")
                )
            except Exception:
                speech_end = {}
            issues = detect_post_tts_deviations(seg, idx, timing_map)
            if speech_end.get("voice_truncated") and not any(
                i.get("code") == "duration_overflow" for i in issues
            ):
                issues.append(
                    {
                        "idx": idx,
                        "code": "duration_overflow",
                        "tts_ms": playback_ms,
                        "slot_ms": slot_ms,
                        "window_ms": slot_ms,
                        "overflow_ms": max(0, playback_ms - slot_ms),
                        "voice_truncated": True,
                        "reason": speech_end.get("reason") or "voice_truncated",
                    }
                )
            overflow_issues = [
                i
                for i in issues
                if i["code"] in ("duration_overflow", "overlap_with_next")
            ]
            underflow_issues = [
                i for i in issues if i["code"] == "duration_underflow"
            ]
            # Drop the deviation that would reverse the locked direction.
            if adapt_direction == "shrink":
                underflow_issues = []
            elif adapt_direction == "expand":
                overflow_issues = []
            if not overflow_issues and not underflow_issues:
                adapt_trace["final_tts_duration_ms"] = playback_ms or first_ms
                break

            stats["deviations"] += 1
            if attempt >= max_retries:
                stats["issues"].extend(overflow_issues or underflow_issues)
                break

            issue = overflow_issues[0] if overflow_issues else underflow_issues[0]
            adapt_direction = "shrink" if overflow_issues else "expand"
            src_hint = source_segments[idx] if idx < len(source_segments) else ""
            original = str(
                seg.get("plain_text")
                or seg.get("translation_text")
                or seg.get("text")
                or ""
            ).strip()
            issue_slot_ms = int(issue.get("slot_ms") or slot_ms)
            tts_ms = int(issue.get("tts_ms") or playback_ms or segment_playback_ms(seg))

            # MASTER TZ v3.0 P2/P10/P11: after LOCK — never rewrite text.
            try:
                from engines.pipeline_integrity.translation_lock import is_segment_locked

                locked = is_segment_locked(seg)
            except Exception:
                locked = bool(seg.get("translation_locked"))
            if locked:
                if overflow_issues:
                    from engines.pipeline_integrity.overflow_manager import (
                        register_overflow,
                    )

                    ov = max(0, tts_ms - issue_slot_ms)
                    register_overflow(
                        seg,
                        index=idx,
                        overflow_ms=ov,
                        slot_ms=issue_slot_ms,
                        reason=str(issue.get("code") or "duration_overflow"),
                    )
                    adapt_trace["reasons"].append(
                        f"{issue['code']}:locked_overflow_manager"
                    )
                else:
                    from engines.pipeline_integrity.underflow_manager import (
                        register_underflow,
                    )

                    shortfall = max(0, issue_slot_ms - tts_ms)
                    register_underflow(
                        seg,
                        index=idx,
                        shortfall_ms=shortfall,
                        slot_ms=issue_slot_ms,
                        audio_ms=tts_ms,
                        reason=str(issue.get("code") or "duration_underflow"),
                    )
                    adapt_trace["reasons"].append(
                        f"{issue['code']}:locked_underflow_manager"
                    )
                stats["issues"].append(
                    {
                        **issue,
                        "retry_failed": "translation_locked",
                        "severity": "warning",
                        "stopped_reason": "post_lock_audio_only",
                    }
                )
                break

            try:
                from engines.translation_adapt import set_llm_context

                set_llm_context(segment=idx, stage=f"post_tts_retry_{attempt + 1}")
            except Exception:
                pass

            if overflow_issues:
                # TZ Adaptive Seg §11: prefer resegment on oversized slots
                # before aggressive text shortening.
                # Happy Path: skip resegment (Stage 3 anti-bleed).
                _did_resegment = False
                _allow_reseg_qa = True
                try:
                    from engines.happy_path import (
                        advanced_adaptation_enabled,
                        task_info_for,
                    )

                    _allow_reseg_qa = bool(
                        advanced_adaptation_enabled(
                            task_info_for(task_id) if task_id else {}
                        )
                    )
                except Exception:
                    _allow_reseg_qa = True
                try:
                    from engines.adaptive_segmentation.post_tts import (
                        should_prefer_resegment,
                        try_split_long_overflow_segment,
                    )

                    _ov = max(0, tts_ms - issue_slot_ms)
                    if (
                        _allow_reseg_qa
                        and should_prefer_resegment(
                            slot_ms=issue_slot_ms,
                            tts_ms=tts_ms,
                            overflow_ms=_ov,
                        )
                    ):
                        adapt_trace["reasons"].append(
                            "resegment_preferred_before_shorten"
                        )
                        _audits_mut = list(audits) if audits is not None else None
                        _did_resegment = try_split_long_overflow_segment(
                            segments_data=segments_data,
                            source_segments=source_segments
                            if isinstance(source_segments, list)
                            else list(source_segments or []),
                            timing_map=timing_map,
                            audits=_audits_mut,
                            idx=idx,
                        )
                        if _did_resegment:
                            if audits is not None and _audits_mut is not None:
                                audits.clear()
                                audits.extend(_audits_mut)
                            adapt_trace["reasons"].append("adaptive_resegment_split")
                            stats["adaptation_executed"] = True
                            stats["resegmented"] = int(stats.get("resegmented") or 0) + 1
                            try:
                                from engines.adaptive_segmentation.retranslate import (
                                    apply_retranslate_if_needed,
                                )
                            except Exception:
                                apply_retranslate_if_needed = None  # type: ignore
                            # Retranslate halves if needed, then TTS — skip shorten
                            if callable(regen_fn):
                                for _ri in (idx, idx + 1):
                                    if _ri >= len(segments_data):
                                        continue
                                    _s = segments_data[_ri]
                                    _src = (
                                        source_segments[_ri]
                                        if isinstance(source_segments, list)
                                        and _ri < len(source_segments)
                                        else src_hint
                                    )
                                    if apply_retranslate_if_needed:
                                        try:
                                            apply_retranslate_if_needed(
                                                _s,
                                                str(_src or ""),
                                                src_lang=src_lang,
                                                tgt_lang=target_lang,
                                            )
                                        except Exception:
                                            pass
                                    _txt = str(
                                        _s.get("plain_text")
                                        or _s.get("text")
                                        or ""
                                    ).strip()
                                    if not _txt:
                                        continue
                                    try:
                                        _rr = regen_fn(
                                            _txt,
                                            voice=voice,
                                            tts_rate=tts_rate,
                                            tts_pitch=tts_pitch,
                                            task_id=task_id,
                                            segment_index=_ri,
                                            segment_id=str(
                                                _s.get("segment_id") or ""
                                            ),
                                        )
                                        if isinstance(_rr, tuple):
                                            _nf, _nms = _rr[0], int(_rr[1] or 0)
                                        else:
                                            _nf, _nms = _rr, 0
                                        if _nf:
                                            _s["file"] = _nf
                                            _s["tts_file_path"] = _nf
                                            if _nms > 0:
                                                _s["playback_duration"] = _nms
                                                _s["tts_ms"] = _nms
                                    except Exception:
                                        pass
                            break
                except Exception as _reseg_exc:
                    adapt_trace["reasons"].append(
                        f"resegment_skipped:{type(_reseg_exc).__name__}"
                    )

                opt = optimize_llm_rephrase_for_slot(
                    original,
                    source_hint=src_hint,
                    slot_ms=issue_slot_ms,
                    tgt_lang=target_lang,
                    max_rounds=1,
                    current_ms=tts_ms,
                )
                stage = "llm_rephrase"
                expansion_strategy = ""
                # TZ v4.0: if LLM unavailable / no change — rule-based compress via DSAL
                if not opt.changed:
                    from engines.dsal import adapt_duration_semantic

                    dsal = adapt_duration_semantic(
                        original,
                        source_hint=src_hint,
                        slot_ms=issue_slot_ms,
                        tgt_lang=target_lang,
                        actual_tts_ms=tts_ms,
                    )
                    if dsal.changed:
                        opt = type(opt)(
                            text=dsal.text,
                            changed=True,
                            budget=opt.budget,
                            stages=opt.stages,
                            stopped_reason="dsal_rule_compress",
                        )
                        stage = "dsal_rule_compress"
            else:
                adapt_trace["expand_required"] = True
                seg["expand_required"] = True
                opt = optimize_expand_for_slot(
                    original,
                    source_hint=src_hint,
                    slot_ms=issue_slot_ms,
                    tgt_lang=target_lang,
                    max_rounds=1,
                    current_ms=tts_ms,
                )
                stage = "llm_expand" if "llm" in (opt.stopped_reason or "") else "dsal_rule_expand"
                expansion_strategy = opt.stopped_reason or "semantic_expansion"
                if opt.changed:
                    expansion_iterations += 1
                    adapt_trace["expand_executed"] = True
                    adapt_trace["expansion_strategy"] = expansion_strategy

            new_text = opt.text if opt.changed else original

            if not new_text or new_text.strip() == original:
                # Mark expand_required for underflow even when no text change possible
                if underflow_issues and not overflow_issues:
                    adapt_trace["expand_required"] = True
                    seg["expand_required"] = True
                    adapt_trace["expansion_strategy"] = (
                        opt.stopped_reason or "dsal_exhausted"
                    )
                # LLM missing is WARNING, not hard stop — keep going with audio-fit later
                stats["issues"].append(
                    {
                        **issue,
                        "retry_failed": "no_text_adaptation",
                        "severity": "warning",
                        "stopped_reason": getattr(opt, "stopped_reason", ""),
                    }
                )
                adapt_trace["reasons"].append(
                    f"{issue['code']}:{getattr(opt, 'stopped_reason', 'no_change')}"
                )
                # Only flag requires_llm if LLM was the intended path and unavailable
                try:
                    from engines.translation_adapt import llm_rephrase_available

                    if not llm_rephrase_available() and overflow_issues:
                        seg["requires_llm_adaptation"] = True
                except Exception:
                    pass
                break

            if regen_fn is None:
                stats["issues"].append({**issue, "retry_failed": "no_regen_fn"})
                break

            regen_result = regen_fn(
                new_text,
                voice=voice,
                tts_rate=tts_rate,
                tts_pitch=tts_pitch,
                task_id=task_id,
                segment_index=idx,
                segment_id=str(seg.get("segment_id") or ""),
            )
            if isinstance(regen_result, tuple):
                new_file, new_ms = regen_result[0], int(regen_result[1] or 0)
            else:
                new_file, new_ms = regen_result, 0

            if not new_file:
                stats["issues"].append({**issue, "retry_failed": "tts_regen_empty"})
                adapt_trace["reasons"].append(f"{issue['code']}:tts_regen_empty")
                break

            seg["text"] = new_text
            seg["plain_text"] = new_text
            seg["translation_text"] = new_text
            seg["file"] = new_file
            seg["tts_file_path"] = new_file
            if new_ms > 0:
                seg["playback_duration"] = new_ms
                seg["tts_ms"] = new_ms

            audit = audit_by_idx.get(idx)
            if audit:
                audit["tts_text"] = new_text
                audit["final_text"] = new_text
                qd = audit.setdefault("quality_details", {})
                qd["post_tts_retry"] = {
                    "attempt": attempt + 1,
                    "reason": issue["code"],
                    "text_before": original[:500],
                    "text_after": new_text[:500],
                    "stage": stage,
                    "expansion_strategy": expansion_strategy or None,
                }

            if commit_fn:
                commit_fn(
                    segments_data, [idx], tts_text=new_text, audio_filename=new_file
                )

            record_post_tts_adaptation(
                None,
                index=idx,
                source_hint=src_hint,
                original=original,
                adapted=new_text,
                reason=f"post_tts_{issue['code']}",
                window_ms=issue_slot_ms,
                tts_ms_before=tts_ms,
                tts_ms_after=new_ms or estimate_tts_duration_ms(new_text, target_lang),
                src_lang=src_lang,
                tgt_lang=target_lang,
            )

            adapt_trace["executed"] = True
            stats["adaptation_executed"] = True
            adapt_trace["iterations"] = int(adapt_trace.get("iterations") or 0) + 1
            adapt_trace["stages"].append(stage)
            adapt_trace["reasons"].append(issue["code"])
            adapt_trace["text_before"] = original[:500]
            adapt_trace["text_after"] = new_text[:500]
            adapt_trace["expansion_iterations"] = expansion_iterations
            adapt_trace["final_tts_duration_ms"] = new_ms or segment_playback_ms(seg)
            _update_speech_timing_diagnostics(
                adapt_trace, seg, slot_ms=slot_ms, start_ms=start_ms
            )

            retry_meta["attempts"] = int(retry_meta.get("attempts") or 0) + 1
            retry_meta["reasons"].append(issue["code"])
            stats["retries"] += 1
            stats["fixed"] += 1
            logger.info(
                "[PostTTS] task=%s seg=%d attempt=%d stage=%s reason=%s "
                "tts_ms=%d→%d slot_ms=%d expand=%s",
                task_id,
                idx,
                attempt + 1,
                stage,
                issue["code"],
                tts_ms,
                new_ms or segment_playback_ms(seg),
                issue_slot_ms,
                expansion_strategy or "-",
            )

        # Final speech-end gate: truncated voice must not count as success.
        try:
            from engines.tts_speech_end import apply_speech_end_to_segment

            final_end = apply_speech_end_to_segment(
                seg,
                wav_path=seg.get("tts_file_path") or seg.get("file"),
                slot_ms=slot_ms,
            )
            if final_end.get("voice_truncated"):
                seg["needs_manual_review"] = True
                retry_meta["truncated"] = True
                retry_meta["manual_review_required"] = True
                adapt_trace["voice_truncated"] = True
                adapt_trace["voice_finished_naturally"] = False
                stats["issues"].append(
                    {
                        "idx": idx,
                        "code": "voice_truncated",
                        "tts_ms": segment_playback_ms(seg),
                        "slot_ms": slot_ms,
                        "severity": "error",
                        "reason": final_end.get("reason") or "voice_truncated",
                    }
                )
        except Exception:
            pass

        adapt_trace["expansion_iterations"] = expansion_iterations

    # ── TZ §3 gate: segments that still require LLM adaptation must never be
    # treated as silently OK. Surface them with a clear, actionable reason.
    try:
        from engines.translation_adapt import llm_rephrase_available

        llm_ok = bool(llm_rephrase_available())
    except Exception:
        llm_ok = False

    pending = [
        i
        for i, seg in enumerate(segments_data)
        if seg.get("merged_into") is None and seg.get("requires_llm_adaptation")
    ]
    if pending:
        reason = (
            "AI-модуль не установлен — интеллектуальная адаптация недоступна"
            if not llm_ok
            else "Не удалось адаптировать текст без потери смысла"
        )
        stats["requires_llm_adaptation"] = {
            "segment_indices": pending,
            "count": len(pending),
            "llm_available": llm_ok,
            "reason": reason,
        }
        logger.warning(
            "[PostTTS] task=%s %d segment(s) still require LLM adaptation (%s): %s — "
            "these reached TTS WITHOUT intelligent rephrase (overflow handled by "
            "gap/video-adapt, never truncation).",
            task_id,
            len(pending),
            reason,
            pending[:20],
        )

    within_goal = 0
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        if not (seg.get("file") or seg.get("tts_file_path")):
            continue
        start_ms, end_ms = (
            _parse_timing(timing_map[idx]) if idx < len(timing_map) else (0, 3000)
        )
        slot_ms = max(1, end_ms - start_ms)
        playback_ms = segment_playback_ms(seg)
        if playback_ms > 0 and segment_duration_matches_goal(playback_ms, slot_ms):
            within_goal += 1
    checked = int(stats.get("checked") or 0)
    stats["duration_match"] = {
        "within_goal": within_goal,
        "total": checked,
        "pct_within_goal": round(100.0 * within_goal / max(checked, 1), 1),
        "goal_ms": DURATION_MATCH_GOAL_MS,
    }

    return stats


def detect_merged_words(text: str) -> list[str]:
    """Detect likely merged-word TTS artifacts (TZ §11 DoD)."""
    t = str(text or "")
    hits: list[str] = []
    for m in _MERGED_WORD_RE.finditer(t):
        hits.append(m.group(0))
    return hits


def detect_text_tts_mismatch(seg: dict, audit: dict | None) -> dict | None:
    """Check final segment text matches text sent to TTS (TZ §11)."""
    spoken = str(seg.get("tts_text") or (audit or {}).get("tts_text") or "").strip()
    canonical = str(
        seg.get("plain_text") or seg.get("translation_text") or seg.get("text") or ""
    ).strip()
    if not spoken or not canonical:
        return None
    spoken_plain = re.sub(r"<[^>]+>", "", spoken).strip()
    if spoken_plain and canonical and spoken_plain != canonical:
        if spoken_plain.replace(" ", "") != canonical.replace(" ", ""):
            return {
                "code": "text_tts_mismatch",
                "spoken": spoken_plain[:200],
                "canonical": canonical[:200],
            }
    return None


def build_final_dub_qa_report(task_info: dict[str, Any]) -> dict[str, Any]:
    """
    Final automatic dub QA after project completion (TZ §11).
    Checks overlaps, pauses, split sentences, meaning, pronunciation hints.
    """
    from engines.cleaner import detect_split_sentences
    from engines.semantic_meaning import verify_meaning_preserved
    from engines.translation_quality import segment_quality_warnings

    segments_data = task_info.get("segments_data") or []
    source_segments = task_info.get("source_segments") or []
    audits = task_info.get("translation_audits") or []
    timing_map = task_info.get("timing_map_backup") or task_info.get("timing_map") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}

    segment_texts = [
        str(s.get("plain_text") or s.get("text") or "")
        for s in segments_data
        if s.get("merged_into") is None
    ]

    issues: list[dict[str, Any]] = []
    ok = True

    for ov in detect_timing_overlaps(timing_map):
        issues.append({**ov, "severity": "error"})
        ok = False
    for lp in detect_long_pauses(timing_map):
        issues.append({**lp, "severity": "warning"})

    for split in detect_split_sentences(segment_texts):
        issues.append({**split, "severity": "error"})
        ok = False

    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        audit = audit_by_idx.get(idx, {})
        src = source_segments[idx] if idx < len(source_segments) else ""
        raw = str(audit.get("raw_translation") or "")
        final = str(
            seg.get("plain_text") or seg.get("text") or audit.get("final_text") or ""
        )
        tts_text = str(seg.get("tts_text") or audit.get("tts_text") or final)

        if not final.strip() and str(src or "").strip():
            issues.append({"index": idx, "code": "lost_sentence", "severity": "error"})
            ok = False

        meaning_ok, reason, hints = verify_meaning_preserved(
            src,
            raw or final,
            tts_text or final,
            target_lang=task_info.get("target_lang"),
        )
        if not meaning_ok and reason not in ("unchanged",):
            issues.append(
                {
                    "index": idx,
                    "code": f"meaning_{reason}",
                    "severity": "error",
                    "hints": hints[:5],
                }
            )
            ok = False

        for w in segment_quality_warnings(
            original=src,
            raw=raw,
            final=final,
            tts_text=tts_text,
            source_lang=task_info.get("detected_lang") or task_info.get("source_lang"),
            target_lang=task_info.get("target_lang"),
            naturalized=str(audit.get("naturalized_text") or ""),
        ):
            if w.get("code") in (
                "preserved_token",
                "empty_translation",
                "semantic_meaning_loss",
                "over_shortening",
                "meaning_change",
            ):
                issues.append({"index": idx, **w, "severity": "warning"})

        merged = detect_merged_words(tts_text or final)
        if merged:
            issues.append(
                {
                    "index": idx,
                    "code": "merged_words",
                    "tokens": merged[:8],
                    "severity": "warning",
                }
            )

        mismatch = detect_text_tts_mismatch(seg, audit)
        if mismatch:
            issues.append({"index": idx, **mismatch, "severity": "error"})
            ok = False

        post_issues = detect_post_tts_deviations(seg, idx, timing_map)
        for pi in post_issues:
            if pi["code"] in ("duration_overflow", "overlap_with_next"):
                issues.append({**pi, "index": idx, "severity": "warning"})

        # Split children must not leave tts_ms=0 in final QA JSON.
        try:
            tts_ms = int(
                seg.get("tts_ms")
                or seg.get("playback_duration")
                or seg.get("final_tts_duration_ms")
                or 0
            )
        except (TypeError, ValueError):
            tts_ms = 0
        if tts_ms <= 0 and str(
            seg.get("final_tts_text") or seg.get("text") or ""
        ).strip():
            issues.append(
                {
                    "index": idx,
                    "code": "tts_ms_zero",
                    "severity": "error",
                }
            )
            ok = False

    video_ms = int(
        task_info.get("video_duration_ms")
        or task_info.get("target_duration_ms")
        or 0
    )
    track_ms = int(task_info.get("track_duration_ms") or 0)
    if track_ms <= 0 and timing_map:
        try:
            track_ms = max(_parse_timing(t)[1] for t in timing_map)
        except Exception:
            track_ms = 0
    tail_gap_ms = max(0, video_ms - track_ms) if video_ms > 0 and track_ms > 0 else 0
    if video_ms > 0 and track_ms > 0 and tail_gap_ms > 500:
        issues.append(
            {
                "code": "track_shorter_than_video",
                "severity": "warning",
                "video_duration_ms": video_ms,
                "track_duration_ms": track_ms,
                "tail_gap_ms": tail_gap_ms,
            }
        )

    report = {
        "ok": ok,
        "issue_count": len(issues),
        "issues": issues,
        "overlap_count": sum(1 for i in issues if i.get("code") == "overlap"),
        "split_sentence_count": sum(
            1 for i in issues if i.get("code") == "split_sentence"
        ),
        "long_pause_count": sum(1 for i in issues if i.get("code") == "long_pause"),
        "video_duration_ms": video_ms or None,
        "track_duration_ms": track_ms or None,
        "tail_gap_ms": tail_gap_ms,
    }
    if video_ms > 0 and track_ms > 0 and tail_gap_ms > 500:
        report["final_status"] = "track_shorter_than_video"
    return report


def _segment_algorithm_reason(seg: dict[str, Any], timing_aware: dict[str, Any]) -> str:
    # PSA7 — diagnostics truth: never claim semantic shorten for audio-only paths
    try:
        from engines.pipeline_integrity.honest_diagnostics import (
            map_segment_algorithm_reason,
        )

        return map_segment_algorithm_reason(seg, timing_aware=timing_aware or {})
    except Exception:
        pass
    if seg.get("text_adaptation_trace", {}).get("executed"):
        return "post_tts_text_adaptation: semantic shorten + TTS regen until slot fit"
    if seg.get("video_adapt_mode") == "gap_absorb":
        return "gap_absorb: overflow into inter-segment gap (no atempo)"
    if seg.get("video_adapt_mode") == "video_adapt":
        return "video_adapt: mild overflow; video stretch candidate"
    if seg.get("merge_adjusted_start"):
        return "block_merge: placement shifted after previous segment speech"
    if seg.get("block_merged_with_next"):
        return "block_merge: borrowed timing from next adjacent slot"
    if timing_aware.get("adapted"):
        return "timing_aware_translation: pre-TTS text budget optimization"
    if seg.get("fitted_file"):
        return "slot_fit: silence trim / pause compress only (no atempo)"
    return "direct_path: TTS fit source slot without text adaptation"


def _build_openddf_source_separation_block(task_info: dict[str, Any]) -> dict[str, Any]:
    try:
        from engines.source_separation import merge_openddf_source_separation

        return merge_openddf_source_separation(task_info)
    except Exception:
        sep = task_info.get("source_separation") or {}
        return {
            "separation_performed": bool(sep.get("attempted")),
            "separation_success": bool(sep.get("success")),
            "fallback_used": bool(sep.get("fallback_used")),
            "dialogue_path": sep.get("dialogue_path"),
            "accompaniment_path": sep.get("accompaniment_path"),
            "warning": sep.get("warning"),
        }


def _build_openddf_tts_pipeline_block(task_info: dict[str, Any]) -> dict[str, Any]:
    """TTS Pipeline lifecycle per segment (TZ §7).

    For each segment: file name, resolved path, on-disk size, existence, plus the
    producer/consumer source locations so a missing file is fully traceable.
    """
    from pathlib import Path as _Path

    segments_data = task_info.get("segments_data") or []
    session_dir = task_info.get("session_dir")
    base = _Path(str(session_dir)) if session_dir else None

    # Static source map of the TTS handoff chain (function · file · line).
    sources = {
        "tts_generated": "engines/dubbing_engine/tts_handoff_diag.py:log_tts_generated",
        "audio_saved": "api/auto_dub_api.py:_commit_tts_group_result",
        "regen": "api/auto_dub_api.py:_regen_segment_tts",
        "slot_fit": "api/auto_dub_api.py:_build_timed_dub_track",
        "track_builder": "api/studio_api.py:_render_studio_timed_audio",
        "cleanup": "engines/pipeline_cleanup.py:cleanup_intermediate_work_dirs",
    }

    rows: list[dict[str, Any]] = []
    present = 0
    try:
        from engines.pipeline_integrity.audio_presence import (
            MIN_AUDIO_BYTES,
            audio_stat,
            resolve_segment_audio_path,
        )
    except Exception:
        MIN_AUDIO_BYTES = 1000
        audio_stat = None
        resolve_segment_audio_path = None

    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        resolved = None
        exists = False
        size = 0
        # Prefer absolute / stamped paths; fall back to session basename lookup.
        cand_path = ""
        if resolve_segment_audio_path is not None:
            try:
                cand_path = resolve_segment_audio_path(seg) or ""
            except Exception:
                cand_path = ""
        if not cand_path:
            name = (
                seg.get("resolved_path")
                or seg.get("fitted_file")
                or seg.get("file")
                or seg.get("tts_file_path")
            )
            if name and base is not None:
                cand_path = str(base / _Path(str(name)).name)
            elif name:
                cand_path = str(name)
        if cand_path:
            p = _Path(cand_path)
            if not p.is_file() and base is not None:
                p2 = base / p.name
                if p2.is_file():
                    p = p2
            resolved = str(p)
            if audio_stat is not None:
                ok, size = audio_stat(p)
                exists = bool(ok)
            else:
                try:
                    if p.is_file():
                        size = int(p.stat().st_size)
                        exists = size >= int(MIN_AUDIO_BYTES)
                except OSError:
                    pass
        if exists:
            present += 1
        rows.append(
            {
                "index": int(seg.get("index", idx)),
                "text_preview": str(seg.get("text") or "")[:80],
                "file": seg.get("file"),
                "fitted_file": seg.get("fitted_file"),
                "resolved_path": resolved,
                "exists": exists,
                "size_bytes": size,
            }
        )

    missing = len(rows) - present
    video_ms = int(
        task_info.get("video_duration_ms")
        or task_info.get("target_duration_ms")
        or 0
    )
    track_ms = int(task_info.get("track_duration_ms") or 0)
    tail_gap_ms = max(0, video_ms - track_ms) if video_ms > 0 and track_ms > 0 else 0
    zero_tts = sum(
        1
        for seg in segments_data
        if seg.get("merged_into") is None
        and str(seg.get("final_tts_text") or seg.get("text") or "").strip()
        and int(
            seg.get("tts_ms")
            or seg.get("playback_duration")
            or seg.get("final_tts_duration_ms")
            or 0
        )
        <= 0
    )
    block = {
        "session_dir": str(session_dir) if session_dir else None,
        "expected_segments": len(rows),
        "audio_present": present,
        "audio_missing": missing,
        "tts_ms_zero": zero_tts,
        "video_duration_ms": video_ms or None,
        "track_duration_ms": track_ms or None,
        "tail_gap_ms": tail_gap_ms,
        "source_map": sources,
        "segments": rows,
        "min_bytes": int(MIN_AUDIO_BYTES),
        "silence_pads": sum(
            1 for seg in segments_data if seg.get("silence_pad")
        ),
    }
    if missing > 0:
        block["final_status"] = "audio_missing_fatal"
    elif video_ms > 0 and track_ms > 0 and tail_gap_ms > 500:
        block["final_status"] = "track_shorter_than_video"
    return block


def _build_openddf_adaptation_capabilities(task_info: dict[str, Any]) -> dict[str, Any]:
    """Surface environment capabilities that gate adaptation/music quality (TZ §7).

    Lets the operator see WHY adaptation or music may be limited (no LLM endpoint,
    demucs missing) instead of guessing.
    """
    import os
    import shutil as _sh

    try:
        from engines.translation_adapt import llm_rephrase_available

        llm_ok = bool(llm_rephrase_available())
    except Exception:
        llm_ok = False

    model = ""
    model_param_b = 0.0
    model_adequate = True
    model_warning = ""
    if llm_ok:
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            _caps = detect_capabilities()
            model = str(_caps.get("model") or "")
            model_param_b = float(_caps.get("model_param_b") or 0.0)
            model_adequate = bool(_caps.get("model_adequate", True))
            model_warning = str(_caps.get("model_warning") or "")
        except Exception:
            pass

    demucs_ok = bool(_sh.which("demucs"))
    sep = task_info.get("source_separation") or {}
    notes: list[str] = []
    if not llm_ok:
        notes.append(
            "AI-модуль не установлен — установите его в Настройках → AI Module "
            "для интеллектуальной адаптации текста."
        )
    elif model_warning:
        # LLM present but too weak — this is the real root cause of rejected
        # rewrites (no_llm_adaptation) and language/meaning drift.
        notes.append(model_warning)
    if not demucs_ok:
        notes.append(
            "demucs not installed — music/vocal separation uses the ffmpeg center/side "
            "approximation (works for stereo sources only)."
        )
    return {
        "llm_rephrase_available": llm_ok,
        "llm_base_url": os.getenv("VM_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "openai-default",
        "llm_model": model,
        "llm_model_param_b": model_param_b,
        "llm_model_adequate": model_adequate,
        "llm_model_warning": model_warning,
        "demucs_available": demucs_ok,
        "separation_method": sep.get("method"),
        "separation_success": bool(sep.get("success")),
        "music_preserved": bool(sep.get("success")),
        "notes": notes,
    }


def _build_openddf_adaptation_mode_block(task_info: dict[str, Any]) -> dict[str, Any]:
    """Selected mode + why the pipeline behaved as it did (TZ §5/§7)."""
    try:
        from engines.llm_adaptation_mode import (
            MODE_STRICT,
            detect_capabilities,
            resolve_adaptation_mode,
        )

        mode = task_info.get("adaptation_mode") or resolve_adaptation_mode(task_info)
        caps = detect_capabilities()
    except Exception:
        mode = task_info.get("adaptation_mode") or "automatic"
        caps = {}
        MODE_STRICT = "strict"  # type: ignore[assignment]

    gate = (task_info.get("post_tts_qa") or {}).get("requires_llm_adaptation") or {}
    stop_diag = task_info.get("llm_gate_diagnostics") or {}
    strict_gate_activated = bool(stop_diag.get("strict_gate_activated")) or (
        mode == MODE_STRICT and bool(gate.get("count"))
    )
    return {
        "mode": mode,
        "mode_label": (
            "Строгий режим" if mode == MODE_STRICT else "Автоматический (рекомендуется)"
        ),
        "capabilities": caps,
        "rule_rewrite_available": bool(caps.get("rule_rewrite_available", True)),
        "llm_rewrite_available": bool(caps.get("llm_available")),
        "llm_provider": caps.get("provider"),
        "llm_model": caps.get("model"),
        "strict_gate_activated": strict_gate_activated,
        "stop_reason": stop_diag.get("reason"),
        "stop_diagnostics": stop_diag or None,
        "user_warnings": list(task_info.get("user_warnings") or []),
        "requires_llm_adaptation": gate or None,
    }


def _build_openddf_storage_report(
    task_info: dict[str, Any], app_dir: Path | None = None
) -> dict[str, Any]:
    """Storage Report for OpenDDF (TZ §10).

    Shows cleanup outcomes: files scanned/deleted, bytes freed, dirs cleaned/skipped.
    """
    from pathlib import Path as _Path

    base = _Path(app_dir) if app_dir else _Path(__file__).resolve().parents[1]
    cleanup = task_info.get("storage_cleanup") or task_info.get("storage_report") or {}

    session_dir = task_info.get("session_dir")
    session_bytes = 0
    session_exists = False
    if session_dir:
        sd = _Path(str(session_dir))
        session_exists = sd.is_dir()
        if session_exists:
            try:
                from engines.model_manager.storage import dir_size

                session_bytes = int(dir_size(sd))
            except Exception:
                pass

    audit_summary = None
    try:
        from engines.storage_audit import audit_storage

        audit = audit_storage(base)
        audit_summary = {
            "total_mb": audit.get("total_mb"),
            "deletable_temp_mb": audit.get("deletable_temp_mb"),
            "buckets": [
                {"id": b["id"], "label": b["label"], "mb": b["mb"]}
                for b in (audit.get("buckets") or [])
            ],
        }
    except Exception:
        pass

    tts = task_info.get("post_tts_qa") or {}
    return {
        "files_created": int(
            cleanup.get("files_created") or cleanup.get("files_scanned") or 0
        ),
        "files_deleted": int(cleanup.get("files_deleted") or 0),
        "bytes_freed": int(cleanup.get("bytes_freed") or 0),
        "mb_freed": cleanup.get("mb_freed"),
        "directories_cleaned": list(cleanup.get("directories_cleaned") or []),
        "directories_skipped": list(cleanup.get("directories_skipped") or []),
        "scope": cleanup.get("scope"),
        "errors": list(cleanup.get("errors") or []),
        "session_dir": str(session_dir) if session_dir else None,
        "session_dir_exists": session_exists,
        "session_dir_bytes": session_bytes,
        "audit_summary": audit_summary,
        "cleanup_performed": bool(cleanup.get("files_deleted")),
        "note": (
            "Временные файлы сессии удалены после успешного дубляжа."
            if cleanup.get("scope") == "after_dub_complete"
            else None
        ),
    }


def _build_openddf_ai_installation_block(task_info: dict[str, Any]) -> dict[str, Any]:
    """OpenDDF «AI Installation» (TubeDub AI Manager v1.0)."""
    try:
        from engines.ai_manager.manager import build_openddf_ai_installation
        from pathlib import Path

        return build_openddf_ai_installation(
            Path(__file__).resolve().parents[1], task_info
        )
    except Exception:
        return {"intelligent_adaptation_available": False}


def build_llm_effectiveness_report(
    task_info: dict[str, Any], segments: list[dict[str, Any]]
) -> dict[str, Any]:
    """Aggregate LLM Effectiveness Report (AutoDub audit TЗ §10).

    Answers: how many segments, how many went through rule-only rewrite, how
    many through LLM rewrite, how many regenerations happened, average attempts,
    and in how many segments the LLM actually improved the result.
    """
    llm_calls = task_info.get("llm_calls") or []
    total = len(segments)
    rule_only = 0
    llm_segments = 0
    improved = 0
    total_attempts = 0
    attempts_counted = 0

    # LLM calls grouped per segment (only real, usable calls prove improvement).
    calls_by_seg: dict[Any, list[dict]] = {}
    for c in llm_calls:
        calls_by_seg.setdefault(c.get("segment"), []).append(c)

    for row in segments:
        idx = row.get("index")
        rule_used = bool(row.get("rule_rewrite_used"))
        llm_used = bool(row.get("llm_rewrite_used"))
        seg_calls = calls_by_seg.get(idx, [])
        # A segment is "LLM-improved" when a usable LLM rewrite was produced and
        # the final adapted text differs from the pre-adaptation text.
        usable_llm = any(c.get("usable") for c in seg_calls) or llm_used
        if usable_llm:
            llm_segments += 1
            if (
                bool(row.get("adaptation_executed"))
                and str(row.get("text_after_adaptation") or "").strip()
                != str(row.get("translated_text") or "").strip()
            ):
                improved += 1
        elif rule_used:
            rule_only += 1

        def _as_int(val: Any) -> int:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        iters = _as_int(row.get("adaptation_iterations"))
        retries_field = row.get("optimization_retries")
        if isinstance(retries_field, dict):
            retries = max(
                (_as_int(v) for v in retries_field.values()),
                default=0,
            )
        else:
            retries = _as_int(retries_field)
        attempts = max(iters, retries)
        if attempts > 0:
            total_attempts += attempts
            attempts_counted += 1

    post_tts = task_info.get("post_tts_qa") or {}
    regenerations = int(post_tts.get("retries") or 0)

    usable_calls = sum(1 for c in llm_calls if c.get("usable"))
    truncated_calls = sum(1 for c in llm_calls if c.get("finish_reason") == "length")

    # Quality/health counts across the final segments (audit §10 AI Adaptation
    # Report). Empty / cut-word / cut-sentence are derived from the pre-TTS
    # integrity gate output when present, else from the final texts.
    from engines.sentence_integrity import validate_tts_text

    empty_segments = 0
    cut_words = 0
    cut_sentences = 0
    repeats = 0
    meaning_errors = 0
    hallucinations = 0
    empty_llm_responses = 0
    identical_responses = 0
    accepted_variants = 0
    rejected_variants = 0
    total_variants = 0
    total_iterations = 0
    iter_count = 0
    slot_fits: list[float] = []
    meaning_scores: list[int] = []
    naturalness_scores: list[int] = []

    llm_not_called = 0
    no_rewrite = 0
    errors = 0
    for row in segments:
        if bool(row.get("llm_needed")) and not bool(row.get("llm_called")):
            llm_not_called += 1
        if bool(row.get("llm_no_rewrite")):
            no_rewrite += 1
        errors += len(row.get("errors") or [])
        final_text = str(
            row.get("final_tts_text") or row.get("text_after_adaptation") or ""
        )
        ok, issues = validate_tts_text(final_text)
        if not ok:
            if "empty" in issues or "null_sentinel" in issues or "empty_json" in issues:
                empty_segments += 1
            if "mid_word" in issues:
                cut_words += 1
            if "incomplete_sentence" in issues or "dangling_connector" in issues:
                cut_sentences += 1
            if "repeats" in issues:
                repeats += 1

        # Parse scores from variant log if present
        vlog = row.get("variant_log") or []
        total_variants += len(vlog)
        for v in vlog:
            if v.get("rejected_reason"):
                rejected_variants += 1
                rr = str(v.get("rejected_reason"))
                if "hallucination" in rr:
                    hallucinations += 1
                if "empty" in rr:
                    empty_llm_responses += 1
                if "identical" in rr:
                    identical_responses += 1
            else:
                accepted_variants += 1
            if v.get("chosen"):
                meaning_scores.append(int(v.get("Meaning Score", 0)))
                naturalness_scores.append(int(v.get("Naturalness Score", 0)))
                break
            if v.get("selected"):
                scores = v.get("scores") or {}
                meaning_scores.append(int(float(scores.get("meaning", 0.0)) * 100))
                naturalness_scores.append(int(float(scores.get("naturalness", 0.0)) * 100))
                break

        ai_trace = row.get("ai_adaptation_trace") or {}
        if ai_trace and not vlog:
            total_variants += len(ai_trace.get("variants") or [])
            total_iterations += int(ai_trace.get("iterations") or 0)
            iter_count += 1
            for v in ai_trace.get("variants") or []:
                rr = str(v.get("rejected_reason") or "")
                if rr:
                    rejected_variants += 1
                    if "hallucination" in rr:
                        hallucinations += 1
                    if "empty" in rr:
                        empty_llm_responses += 1
                    if "identical" in rr:
                        identical_responses += 1
                else:
                    accepted_variants += 1
                scores = v.get("scores") or {}
                if v.get("selected"):
                    meaning_scores.append(int(float(scores.get("meaning", 0.0)) * 100))
                    naturalness_scores.append(int(float(scores.get("naturalness", 0.0)) * 100))
        elif ai_trace:
            total_iterations += int(ai_trace.get("iterations") or 0)
            iter_count += 1

        slot = int(row.get("original_duration_ms") or 0)
        played = int(row.get("final_tts_duration_ms") or 0)
        if slot > 0 and played > 0:
            slot_fits.append(round(min(played, slot) / max(played, slot), 3))

        # Count meaning errors
        if float(row.get("meaning_loss_score") or 0) > 0:
            meaning_errors += 1

    avg_slot_fit = round(sum(slot_fits) / len(slot_fits), 3) if slot_fits else 0.0
    avg_meaning_score = (
        round(sum(meaning_scores) / len(meaning_scores), 2) if meaning_scores else 0.0
    )
    avg_naturalness_score = (
        round(sum(naturalness_scores) / len(naturalness_scores), 2)
        if naturalness_scores
        else 0.0
    )
    avg_variants = round(total_variants / max(1, total), 2)
    avg_iterations = round(total_iterations / iter_count, 2) if iter_count else 0.0

    total_ms = sum(float(c.get("ms") or 0) for c in llm_calls)
    avg_gen_time = round(total_ms / len(llm_calls), 2) if llm_calls else 0.0

    return {
        "ai_adaptation_effectiveness_report": {
            "Общее количество сегментов": total,
            "Количество вызовов LLM": len(llm_calls),
            "Среднее количество вариантов": avg_variants,
            "Среднее число итераций": avg_iterations,
            "Среднее количество попыток": (
                round(total_attempts / attempts_counted, 2) if attempts_counted else 0.0
            ),
            "Среднее время генерации": avg_gen_time,
            "Количество Rule Rewrite": rule_only,
            "Количество LLM Rewrite": llm_segments,
            "Количество Hallucinations": hallucinations,
            "Количество пустых сегментов": empty_segments,
            "Количество пустых ответов": empty_llm_responses,
            "Количество одинаковых ответов": identical_responses,
            "Количество отсутствия Rewrite": no_rewrite,
            "Количество оборванных слов": cut_words,
            "Количество оборванных предложений": cut_sentences,
            "Количество повторений": repeats,
            "Количество ошибок смысла": meaning_errors,
            "Средний Slot Fit": avg_slot_fit,
            "Средний Meaning Score": avg_meaning_score,
            "Средний Naturalness Score": avg_naturalness_score,
            "Количество принятых вариантов": accepted_variants,
            "Количество отклонённых вариантов": rejected_variants,
        },
        "segment_count": total,
        "rule_rewrite_only": rule_only,
        "llm_rewrite_used": llm_segments,
        "regenerations": regenerations,
        "avg_attempts": (
            round(total_attempts / attempts_counted, 2) if attempts_counted else 0.0
        ),
        "llm_improved_segments": improved,
        "llm_calls_total": len(llm_calls),
        "llm_calls_usable": usable_calls,
        "llm_calls_truncated": truncated_calls,
        "llm_available": bool(llm_calls),
        # AI Adaptation Report extras (audit §10)
        "llm_not_called_segments": llm_not_called,
        "no_rewrite_segments": no_rewrite,
        "error_count": errors,
        "empty_segments": empty_segments,
        "cut_word_segments": cut_words,
        "cut_sentence_segments": cut_sentences,
        "avg_slot_fit": avg_slot_fit,
        "avg_variants": avg_variants,
        "avg_iterations": avg_iterations,
        "hallucinations": hallucinations,
        "empty_llm_responses": empty_llm_responses,
        "identical_llm_responses": identical_responses,
        "accepted_variants": accepted_variants,
        "rejected_variants": rejected_variants,
    }


def build_llm_diagnostics(task_info: dict[str, Any]) -> dict[str, Any]:
    """LLM Diagnostics section (audit §9): provider/model/prompt/response/time."""
    llm_calls = task_info.get("llm_calls") or []
    providers = sorted(
        {str(c.get("provider") or "") for c in llm_calls if c.get("provider")}
    )
    models = sorted({str(c.get("model") or "") for c in llm_calls if c.get("model")})
    total_ms = sum(float(c.get("ms") or 0) for c in llm_calls)
    skip_reasons: dict[str, int] = {}
    for s in task_info.get("llm_status") or []:
        r = s.get("skip_reason")
        if r:
            skip_reasons[r] = skip_reasons.get(r, 0) + 1
    return {
        "llm_called": bool(llm_calls),
        "call_count": len(llm_calls),
        "providers": providers,
        "models": models,
        "total_generation_ms": round(total_ms, 1),
        "avg_call_ms": round(total_ms / len(llm_calls), 1) if llm_calls else 0.0,
        "skip_reasons": skip_reasons,
        "calls": llm_calls,
    }


def _build_openddf_ai_core_block(task_info: dict[str, Any]) -> dict[str, Any]:
    """AI Core Report section (full decision history). Never raises."""
    try:
        from engines.ai_core.report import build_ai_core_report

        return build_ai_core_report(task_info)
    except Exception:  # pragma: no cover - defensive
        return {"enabled": False}


def build_openddf_full_report(task_info: dict[str, Any]) -> dict[str, Any]:
    """Full OpenDDF diagnostic report for UI (TubeDub 2.0 stage 2)."""
    segments = build_openddf_segment_diagnostics(task_info)
    segments_data = task_info.get("segments_data") or []
    timing_map = task_info.get("timing_map_backup") or task_info.get("timing_map") or []

    # Attach the per-segment LLM call log (text sent → text received) so the UI
    # can prove each call and show why a version was chosen (audit §1/§2/§3/§9).
    _llm_calls = task_info.get("llm_calls") or []
    _calls_by_seg: dict[Any, list[dict]] = {}
    for _c in _llm_calls:
        _calls_by_seg.setdefault(_c.get("segment"), []).append(_c)
    _status_by_seg: dict[Any, dict] = {}
    for _s in task_info.get("llm_status") or []:
        _status_by_seg[_s.get("segment")] = _s
    _llm_available = False
    _provider_fatal = False
    try:
        from engines.llm_callable import get_run_state
        from engines.translation_adapt import llm_rephrase_available

        _run = get_run_state()
        _llm_available = bool(_run.get("callable")) if _run.get("checked_at") else bool(
            llm_rephrase_available()
        )
        _provider_fatal = bool(_run.get("fatal_reason")) and not _llm_available
    except Exception:
        pass
    _post_tts_failed: dict[int, str] = {}
    for _iss in (task_info.get("post_tts_qa") or {}).get("issues") or []:
        if _iss.get("retry_failed"):
            _post_tts_failed[int(_iss.get("idx", -1))] = str(_iss.get("retry_failed"))
    for _row in segments:
        _idx = _row.get("index")
        _row["llm_calls"] = _calls_by_seg.get(_idx, [])
        _row["llm_called"] = bool(_row["llm_calls"])
        _st = _status_by_seg.get(_idx, {})
        _row["llm_needed"] = bool(_st.get("needed")) or bool(
            _row.get("requires_llm_adaptation")
        )
        _row["llm_skip_reason"] = _st.get("skip_reason")
        _row["llm_no_rewrite"] = bool(_st.get("no_rewrite"))
        _row["llm_attempts"] = int(_st.get("attempts") or 0)
        # Never a silent skip (audit §2): a segment that needed LLM but was
        # never called carries an explicit LLM_NOT_CALLED diagnostic error.
        _errors = list(_row.get("errors") or [])
        _seg_calls = _row.get("llm_calls") or []
        _all_calls_failed = bool(_seg_calls) and all(
            str(c.get("finish_reason") or "") == "error" or not c.get("usable")
            for c in _seg_calls
        )
        if _row["llm_needed"] and not _row["llm_called"]:
            _fail = _post_tts_failed.get(int(_idx or -1))
            _skip = str(_st.get("skip_reason") or "")
            _trace = (_row.get("ai_adaptation_trace") or {})
            _provider_fatal = bool(_trace.get("provider_fatal")) or _skip in (
                "provider_fatal",
                "model_missing",
            ) or _provider_fatal
            if _provider_fatal:
                _errors.append(
                    {
                        "code": "LLM_PROVIDER_FATAL",
                        "reason": _skip or "provider_fatal",
                        "message": (
                            "Провайдер LLM недоступен — интеллектуальная адаптация "
                            "не выполнена после всех попыток инициализации"
                        ),
                    }
                )
            elif not _llm_available:
                _errors.append(
                    {
                        "code": "LLM_UNAVAILABLE",
                        "reason": "llm_unavailable",
                        "message": "AI-модуль недоступен — интеллектуальная адаптация невозможна",
                    }
                )
            elif _skip in ("llm_circuit_open", "segment_breaker_open", "model_too_slow"):
                _errors.append(
                    {
                        "code": "LLM_CIRCUIT_OPEN",
                        "reason": _skip,
                        "message": "LLM отключена после повторных таймаутов — сегмент обработан без адаптации",
                    }
                )
            elif _fail == "no_llm_adaptation":
                _errors.append(
                    {
                        "code": "LLM_ADAPTATION_FAILED",
                        "reason": _fail,
                        "message": "Адаптация не удалась без потери смысла",
                    }
                )
            else:
                _errors.append(
                    {
                        "code": "LLM_NOT_CALLED",
                        "reason": _skip or "unknown",
                        "message": "Сегмент требовал интеллектуальной адаптации, но LLM не была вызвана",
                    }
                )
        elif _row["llm_called"] and _all_calls_failed:
            _errors.append(
                {
                    "code": "LLM_CALL_FAILED",
                    "reason": "timeout_or_error",
                    "message": "LLM была вызвана, но все попытки завершились ошибкой или таймаутом",
                }
            )
        if _row["llm_no_rewrite"] and not _all_calls_failed:
            _errors.append(
                {
                    "code": "NO_REWRITE_PERFORMED",
                    "reason": _st.get("no_rewrite_reason") or "identical_output",
                    "message": "LLM была вызвана, но вернула идентичный текст",
                }
            )
        _row["errors"] = _errors

    skipped_segments: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is None:
            continue
        skipped_segments.append(
            {
                "index": idx,
                "merged_into": seg.get("merged_into"),
                "merged_into_id": seg.get("merged_into_id"),
                "text": str(seg.get("text") or "")[:300],
                "reason": "merged_into_neighbor_tts_group",
            }
        )

    overlaps: list[dict[str, Any]] = []
    for row in segments:
        oi = row.get("overlap_info") or {}
        if int(oi.get("overflow_ms") or 0) > DURATION_TOLERANCE_MS:
            overlaps.append(
                {
                    "index": row.get("index"),
                    "overflow_ms": oi.get("overflow_ms"),
                    "type": "segment_overflow",
                }
            )
    for issue in detect_timing_overlaps(timing_map):
        overlaps.append(
            {"index": issue.get("index"), **issue, "type": "timing_map_overlap"}
        )

    for i in range(len(segments) - 1):
        a = segments[i]
        b = segments[i + 1]
        end_a = int(a.get("start_time_ms") or 0) + int(
            a.get("final_tts_duration_ms") or 0
        )
        start_b = int(b.get("start_time_ms") or 0)
        if end_a > start_b + OVERLAP_TOLERANCE_MS:
            overlaps.append(
                {
                    "index": a.get("index"),
                    "next_index": b.get("index"),
                    "overlap_ms": end_a - start_b,
                    "type": "audio_placement_overlap",
                }
            )

    any_adaptation = any(
        s.get("adaptation_executed") or s.get("dsal_applied") for s in segments
    )
    post_qa = task_info.get("post_tts_qa") or {}
    if not any_adaptation and (
        post_qa.get("adaptation_executed")
        or int(post_qa.get("rewritten") or 0) > 0
    ):
        any_adaptation = True
    pre_dsal = task_info.get("dsal_pre_lock") or {}
    if not any_adaptation and (
        pre_dsal.get("adapted")
        or int(pre_dsal.get("adapted") or 0) > 0
        or pre_dsal.get("adaptation_executed")
    ):
        any_adaptation = True
    overlap_detected = bool(overlaps)
    timing_mismatch = any(
        s.get("timing_source") not in (None, "timing_map")
        or s.get("block_merge", {}).get("merge_adjusted_start")
        for s in segments
    )

    return {
        "task_id": task_info.get("task_id") or task_info.get("mux_base_id"),
        "target_lang": task_info.get("target_lang"),
        "segments": segments,
        "skipped_segments": skipped_segments,
        "overlaps": overlaps,
        "post_tts_qa": task_info.get("post_tts_qa") or {},
        "pre_tts_integrity": task_info.get("pre_tts_integrity") or {},
        "llm_effectiveness": build_llm_effectiveness_report(task_info, segments),
        "llm_diagnostics": build_llm_diagnostics(task_info),
        "tts_pipeline": (
            task_info.get("tts_pipeline")
            if isinstance(task_info.get("tts_pipeline"), dict)
            and int((task_info.get("tts_pipeline") or {}).get("expected_segments") or 0) > 0
            else _build_openddf_tts_pipeline_block(task_info)
        ),
        "adaptation_mode": _build_openddf_adaptation_mode_block(task_info),
        "adaptation_capabilities": _build_openddf_adaptation_capabilities(task_info),
        "storage_report": _build_openddf_storage_report(task_info),
        "ai_installation": _build_openddf_ai_installation_block(task_info),
        "pipeline_stages": task_info.get("pipeline_stages") or {},
        "runtime_pipeline": task_info.get("runtime_pipeline_summary"),
        "final_dub_qa": task_info.get("final_dub_qa"),
        "source_separation": _build_openddf_source_separation_block(task_info),
        "ai_core_report": _build_openddf_ai_core_block(task_info),
        "decision_trace_summary": {
            "title": "Decision Trace",
            "segments_with_trace": sum(
                1 for s in segments if (s.get("decision_trace") or {}).get("stages")
            ),
            "failed_final": [
                {
                    "index": s.get("index"),
                    "segment_id": s.get("segment_id"),
                    "summary": (s.get("decision_trace") or {}).get("summary"),
                    "skip_reason": s.get("adaptation_skip_reason"),
                }
                for s in segments
                if any(
                    st.get("name") == "final_result" and st.get("status") == "FAILED"
                    for st in ((s.get("decision_trace") or {}).get("stages") or [])
                )
            ],
        },
        "summary": {
            "segment_count": len(segments),
            "skipped_count": len(skipped_segments),
            "overlap_count": len(overlaps),
            "adaptation_status": (
                "ADAPTATION EXECUTED" if any_adaptation else "ADAPTATION NOT EXECUTED"
            ),
            "overlap_status": (
                "OVERLAP DETECTED" if overlap_detected else "NO OVERLAP"
            ),
            "timing_status": ("TIMING MISMATCH" if timing_mismatch else "TIMING OK"),
        },
        "flags": [
            "ADAPTATION NOT EXECUTED" if not any_adaptation else "ADAPTATION EXECUTED",
            "OVERLAP DETECTED" if overlap_detected else None,
            "TIMING MISMATCH" if timing_mismatch else None,
        ],
    }


_RULE_REWRITE_STAGES = {"minimal", "moderate", "strong", "semantic_rephrase", "rule"}
_LLM_REWRITE_STAGES = {"llm_rephrase", "llm_expand", "llm"}


def _stage_name(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("stage") or entry.get("name") or "").lower()
    return str(getattr(entry, "stage", "") or "").lower()


def _detect_rewrite_usage(
    stage_lists: list[list[Any]], reasons: list[str]
) -> dict[str, Any]:
    """Classify which rewrite engines actually ran for a segment (TZ §5)."""
    rule_used = False
    llm_used = False
    llm_reason = None
    for stages in stage_lists:
        for entry in stages or []:
            name = _stage_name(entry)
            if name in _RULE_REWRITE_STAGES:
                rule_used = True
            if name in _LLM_REWRITE_STAGES:
                llm_used = True
    joined = " ".join(str(r) for r in (reasons or [])).lower()
    if "llm" in joined:
        llm_reason = next((r for r in reasons if "llm" in str(r).lower()), None)
        if "requires_llm" in joined or "requires_llm_expansion" in joined:
            llm_used = llm_used  # flagged but not necessarily applied
    return {
        "rule_rewrite_used": rule_used,
        "llm_rewrite_used": llm_used,
        "llm_reason": llm_reason,
    }


def build_openddf_segment_diagnostics(
    task_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Per-segment diagnostic bundle for OpenDDF (TZ §13).
    """
    from engines.semantic_adaptation import estimate_tts_duration_ms

    segments_data = task_info.get("segments_data") or []
    audits = task_info.get("translation_audits") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}
    source_segments = task_info.get("source_segments") or []
    voice = task_info.get("voice") or task_info.get("tts_voice") or ""
    target_lang = task_info.get("target_lang") or ""

    rows: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        audit = audit_by_idx.get(idx, {})
        qd = audit.get("quality_details") or {}
        timing_aware = qd.get("timing_aware") or {}
        post_retry = seg.get("post_tts_retry") or qd.get("post_tts_retry") or {}
        adapt_trace = seg.get("text_adaptation_trace") or {}

        final_tts = str(
            seg.get("tts_text")
            or audit.get("tts_text")
            or seg.get("plain_text")
            or seg.get("text")
            or ""
        )
        pre_tts = str(
            audit.get("pre_tts_text")
            or timing_aware.get("text_after")
            or audit.get("final_text")
            or seg.get("plain_text")
            or ""
        )
        semantic_text = str(
            audit.get("semantic_text") or audit.get("naturalized_text") or ""
        )
        raw_mt = str(audit.get("raw_translation") or "")
        original = (
            source_segments[idx]
            if idx < len(source_segments)
            else str(audit.get("whisper_text") or "")
        )

        slot_ms = int(seg.get("slot_ms") or 0)
        start_ms = int(adapt_trace.get("start_time_ms") or seg.get("start_ms") or 0)
        end_ms = int(adapt_trace.get("end_time_ms") or seg.get("end_ms") or 0)
        if end_ms <= start_ms and idx < len(task_info.get("timing_map") or []):
            from engines.timing_fit import _parse_timing

            start_ms, end_ms = _parse_timing((task_info.get("timing_map") or [])[idx])

        # Derive slot from start/end when segment.slot_ms is missing (fixes false overflows).
        if slot_ms <= 0 and end_ms > start_ms:
            slot_ms = max(1, end_ms - start_ms)
        if slot_ms <= 0 and idx < len(task_info.get("timing_map") or []):
            from engines.timing_fit import _parse_timing

            s, e = _parse_timing((task_info.get("timing_map") or [])[idx])
            slot_ms = max(1, e - s)
            if end_ms <= start_ms and e > s:
                start_ms, end_ms = s, e

        if slot_ms > 0 and int(seg.get("slot_ms") or 0) <= 0:
            seg["slot_ms"] = slot_ms
        # Timing ownership: stamp start/end via Scheduler when identity exists.
        if end_ms > start_ms and str(seg.get("segment_id") or "").strip():
            if seg.get("start_ms") is None or seg.get("end_ms") is None:
                try:
                    from engines.scheduler import update_time

                    update_time(
                        [seg],
                        str(seg["segment_id"]),
                        start_ms=start_ms,
                        end_ms=end_ms,
                        info=task_info if isinstance(task_info, dict) else None,
                    )
                except Exception:
                    pass

        predicted_ms = int(
            timing_aware.get("predicted_ms_after")
            or qd.get("predicted_ms")
            or estimate_tts_duration_ms(final_tts, target_lang)
        )
        actual_ms = segment_playback_ms(seg)
        if actual_ms <= 0:
            actual_ms = int(
                adapt_trace.get("final_tts_duration_ms")
                or adapt_trace.get("first_tts_duration_ms")
                or post_retry.get("tts_ms")
                or 0
            )
        if actual_ms <= 0:
            for iss in (task_info.get("post_tts_qa") or {}).get("issues") or []:
                if int(iss.get("idx", -1)) == idx:
                    actual_ms = int(iss.get("tts_ms") or 0)
                    if actual_ms > 0:
                        break
        chain = qd.get("transformation_chain") or {}
        qa = qd.get("quality_analysis") or {}

        ada_meta = task_info.get("adaptive_dubbing_adapter") or {}
        ada_audits = ada_meta.get("segment_audit") or []
        ada_audit_row = ada_audits[idx] if idx < len(ada_audits) else {}
        variant_log = ada_audit_row.get("variant_log") or []

        timing_meta = seg.get("timing_meta") or {}
        ai_trace = (
            seg.get("ai_adaptation_trace")
            or timing_aware.get("ai_adaptation_trace")
            or {}
        )
        ai_variants = list(ai_trace.get("variants") or [])
        if ai_variants:
            variant_log = ai_variants
        ai_executed = bool(ai_trace.get("llm_called")) or bool(ai_trace.get("validation_passed"))
        _rewrite_usage = _detect_rewrite_usage(
            [
                adapt_trace.get("stages") or [],
                timing_aware.get("optimization_stages") or [],
                ai_trace.get("stages") or [],
            ],
            list(adapt_trace.get("reasons") or [])
            + list((post_retry.get("reasons") or []))
            + ([str(ai_trace.get("chosen_reason") or "")] if ai_trace else []),
        )
        if seg.get("requires_llm_adaptation"):
            _rewrite_usage["requires_llm_adaptation"] = True

        # Recompute overflow from real slot + TTS (ignore stale overflow when slot was 0).
        if slot_ms > 0 and actual_ms > 0:
            overflow_ms = max(0, int(actual_ms) - int(slot_ms))
            overflow_pct = round(100.0 * overflow_ms / max(slot_ms, 1), 1)
            slot_overflow = overflow_ms > DURATION_TOLERANCE_MS
        else:
            overflow_ms = int(seg.get("overflow_ms") or 0) if slot_ms > 0 else 0
            overflow_pct = float(seg.get("overflow_pct") or 0.0) if slot_ms > 0 else 0.0
            slot_overflow = bool(seg.get("slot_overflow")) if slot_ms > 0 else False

        try:
            from engines.dub_engine_v2.adaptation_decision import (
                finalize_segment_adaptation_fields,
            )

            if overflow_ms > 0:
                seg["overflow_ms"] = overflow_ms
            finalize_segment_adaptation_fields(seg, index=idx)
        except Exception:
            pass

        _adapt_executed = (
            bool(adapt_trace.get("executed"))
            or ai_executed
            or bool(seg.get("adaptation_executed"))
        )
        _skip_reason = str(
            seg.get("adaptation_skip_reason")
            or (seg.get("adaptation_decision") or {}).get("skip_reason")
            or ""
        )
        try:
            from engines.dub_engine_v2.decision_trace import format_decision_trace_openddf

            _decision_trace = format_decision_trace_openddf(seg)
        except Exception:
            _decision_trace = {
                "title": "Decision Trace",
                "stages": [],
                "transitions": [],
                "summary": "",
            }

        rows.append(
            {
                "index": idx,
                "segment_id": seg.get("segment_id"),
                "skipped": False,
                "merged_into": seg.get("merged_into"),
                "path_chain": (
                    chain.get("chain") or qa.get("transformation_chain") or []
                ),
                "original_text": original,
                "translated_text": str(
                    audit.get("final_text") or audit.get("naturalized_text") or ""
                ),
                "text_after_adaptation": adapt_trace.get("text_after") or final_tts,
                "adaptation_iterations": int(
                    ai_trace.get("iterations") or adapt_trace.get("iterations") or 0
                ),
                "adaptation_executed": _adapt_executed,
                "adaptation_status": (
                    "ADAPTATION EXECUTED"
                    if _adapt_executed
                    else "ADAPTATION NOT EXECUTED"
                ),
                "adaptation_skip_reason": "" if _adapt_executed else _skip_reason,
                "adaptation_decision": dict(seg.get("adaptation_decision") or {}),
                "decision_trace": _decision_trace,
                "original_duration_ms": int(
                    adapt_trace.get("original_duration_ms") or slot_ms
                ),
                "first_tts_duration_ms": int(
                    adapt_trace.get("first_tts_duration_ms") or 0
                ),
                "final_tts_duration_ms": int(
                    adapt_trace.get("final_tts_duration_ms") or actual_ms
                ),
                "start_time_ms": start_ms,
                "end_time_ms": end_ms,
                "timing_source": adapt_trace.get("timing_source") or "timing_map",
                "warnings": list(seg.get("validation_warnings") or []),
                "adaptation_reasons": list(adapt_trace.get("reasons") or []),
                "adaptation_stages": list(adapt_trace.get("stages") or []) + list(ai_trace.get("stages") or []),
                "variant_log": variant_log,
                "overlap_info": {
                    "overflow_ms": overflow_ms,
                    "overflow_pct": overflow_pct,
                    "slot_overflow": slot_overflow,
                    "merge_adjusted_start": seg.get("merge_adjusted_start"),
                },
                "merge_info": {
                    "merged_into": seg.get("merged_into"),
                    "merged_into_id": seg.get("merged_into_id"),
                    "block_merged_with_next": seg.get("block_merged_with_next"),
                },
                "gap_absorb": {
                    "mode": seg.get("video_adapt_mode"),
                    "video_stretch_ratio": seg.get("video_stretch_ratio"),
                },
                "block_merge": {
                    "merge_adjusted_start": seg.get("merge_adjusted_start"),
                    "merge_adjusted_slot_ms": seg.get("merge_adjusted_slot_ms"),
                    "block_merged_with_next": seg.get("block_merged_with_next"),
                },
                "algorithm_reason": _segment_algorithm_reason(seg, timing_aware),
                "rule_rewrite_used": _rewrite_usage["rule_rewrite_used"],
                "llm_rewrite_used": _rewrite_usage["llm_rewrite_used"],
                "llm_reason": _rewrite_usage.get("llm_reason"),
                "requires_llm_adaptation": bool(seg.get("requires_llm_adaptation")),
                "raw_translation": raw_mt,
                "semantic_engine_text": semantic_text,
                "pre_tts_text": pre_tts,
                "final_tts_text": final_tts,
                "original_word_count": chain.get("original_word_count")
                or qa.get("word_counts", {}).get("original"),
                "raw_mt_word_count": chain.get("raw_mt_word_count")
                or qa.get("word_counts", {}).get("raw_mt"),
                "semantic_word_count": chain.get("semantic_word_count")
                or qa.get("word_counts", {}).get("baseline"),
                "final_tts_word_count": chain.get("final_tts_word_count")
                or qa.get("word_counts", {}).get("tts"),
                "compression_ratio": chain.get("compression_ratio"),
                "predicted_duration_ms": predicted_ms,
                "estimated_speech_duration_ms": chain.get(
                    "estimated_speech_duration_ms"
                )
                or predicted_ms,
                "actual_duration_ms": actual_ms,
                "real_speech_duration_ms": actual_ms,
                "meaning_loss_score": chain.get("meaning_loss_score")
                or qa.get("meaning_loss_score"),
                "entity_preservation_score": chain.get("entity_preservation_score"),
                "slot_ms": slot_ms,
                "voice": voice,
                "language": target_lang,
                "raw_mt_diagnosis": qa.get("raw_mt_diagnosis"),
                "quality_reasons": qa.get("reasons"),
                "transformation_chain": chain.get("chain") or [],
                "optimization_stages": timing_aware.get("optimization_stages") or [],
                "ai_adaptation_trace": ai_trace,
                "llm_attempts": int(
                    ai_trace.get("llm_calls")
                    or seg.get("llm_attempts")
                    or timing_aware.get("iterations")
                    or 0
                ),
                "optimization_retries": {
                    "timing_aware": timing_aware.get("iterations"),
                    "post_tts": post_retry.get("attempts"),
                    "reasons": list(post_retry.get("reasons") or []),
                },
                "original_speech_end_ms": int(
                    adapt_trace.get("original_speech_end_ms") or (start_ms + slot_ms)
                ),
                "tts_speech_end_ms": int(
                    adapt_trace.get("tts_speech_end_ms") or (start_ms + actual_ms)
                ),
                "speech_difference_ms": int(
                    adapt_trace.get("speech_difference_ms") or (slot_ms - actual_ms)
                ),
                "expand_required": bool(adapt_trace.get("expand_required")),
                "expand_executed": bool(
                    adapt_trace.get("expand_executed") or seg.get("expand_executed")
                ),
                "shorten_executed": bool(seg.get("shorten_executed")),
                "split_executed": bool(
                    seg.get("split_executed")
                    or seg.get("stage19c_split_done")
                    or seg.get("stage19e_split_done")
                ),
                "post_restore_split": bool(
                    seg.get("post_restore_split")
                    or (seg.get("stage19e") or {}).get("post_restore_split")
                ),
                "split_children": int(
                    (seg.get("stage19e") or {}).get("split_children")
                    or (seg.get("stage19c") or {}).get("split_children")
                    or 0
                ),
                "truncation_blocked": bool(seg.get("truncation_blocked")),
                "retention_score": seg.get("retention_score"),
                "fill_ratio": seg.get("fill_ratio"),
                "rewrite_iterations": int(
                    (seg.get("closed_loop") or {}).get("iterations")
                    or seg.get("rewrite_iterations")
                    or 0
                ),
                "expansion_strategy": (
                    adapt_trace.get("expansion_strategy")
                    or seg.get("expansion_strategy")
                    or ""
                ),
                "stage19d": seg.get("stage19d") or {},
                "stage19e": seg.get("stage19e") or {},
                "stage19f": seg.get("stage19f") or {},
                "stage19g": seg.get("stage19g") or {},
                "stage19h": seg.get("stage19h") or {},
                "stage19i": seg.get("stage19i") or {},
                "stage19j": seg.get("stage19j") or {},
                "stage21": seg.get("stage21") or {},
                "stage22": seg.get("stage22") or {},
                "text_changed": bool(
                    seg.get("text_changed")
                    or (seg.get("stage22") or {}).get("text_changed")
                    or (seg.get("stage21") or {}).get("text_changed")
                    or (seg.get("stage19j") or {}).get("text_changed")
                    or (seg.get("stage19i") or {}).get("text_changed")
                    or (seg.get("stage19h") or {}).get("text_changed")
                    or (seg.get("stage19g") or {}).get("text_changed")
                ),
                "unique_text_ok": bool(
                    seg.get("unique_text_ok")
                    if seg.get("unique_text_ok") is not None
                    else (
                        seg.get("stage21")
                        or seg.get("stage19j")
                        or seg.get("stage19i")
                        or seg.get("stage19h")
                        or {}
                    ).get("unique_text_ok", True)
                ),
                "clean_split_ok": bool(
                    seg.get("clean_split_ok")
                    if seg.get("clean_split_ok") is not None
                    else (seg.get("stage21") or seg.get("stage19j") or {}).get(
                        "clean_split_ok", True
                    )
                ),
                "garbage_expand_blocked": int(
                    seg.get("garbage_expand_blocked")
                    or (seg.get("stage21") or {}).get("garbage_expand_blocked")
                    or (seg.get("stage19j") or {}).get("garbage_expand_blocked")
                    or 0
                ),
                "force_split_executed": bool(
                    (seg.get("stage21") or {}).get("force_split_executed")
                    or seg.get("force_split_executed")
                    or seg.get("split_executed")
                ),
                "char_budget": int(
                    (
                        seg.get("stage21")
                        or seg.get("stage19j")
                        or seg.get("stage19i")
                        or {}
                    ).get("char_budget")
                    or seg.get("char_budget")
                    or 0
                ),
                "estimated_cps": float(
                    (
                        seg.get("stage21")
                        or seg.get("stage19j")
                        or seg.get("stage19i")
                        or {}
                    ).get("estimated_cps")
                    or seg.get("estimated_cps")
                    or 0
                ),
                "soft_pad_count": int(
                    (
                        seg.get("stage21")
                        or seg.get("stage19j")
                        or seg.get("stage19i")
                        or {}
                    ).get("soft_pad_count")
                    or seg.get("soft_pad_count")
                    or 0
                ),
                "stage19g_split_depth": int(
                    seg.get("stage19g_split_depth")
                    or (seg.get("stage19g") or {}).get("stage19g_split_depth")
                    or 0
                ),
                "stage19h_split_depth": int(
                    seg.get("stage19h_split_depth")
                    or (seg.get("stage19h") or {}).get("stage19h_split_depth")
                    or seg.get("stage19g_split_depth")
                    or 0
                ),
                "stage19i_split_depth": int(
                    seg.get("stage19i_split_depth")
                    or (seg.get("stage19i") or {}).get("stage19i_split_depth")
                    or seg.get("stage19h_split_depth")
                    or 0
                ),
                "stage19j_split_depth": int(
                    seg.get("stage19j_split_depth")
                    or (seg.get("stage19j") or {}).get("stage19j_split_depth")
                    or seg.get("stage19i_split_depth")
                    or 0
                ),
                "stage21_split_depth": int(
                    seg.get("stage21_split_depth")
                    or (seg.get("stage21") or {}).get("stage21_split_depth")
                    or seg.get("stage19j_split_depth")
                    or 0
                ),
                "overflow_ms": int(
                    (seg.get("stage21") or {}).get("overflow_ms")
                    or seg.get("overflow_ms")
                    or 0
                ),
                "duration_match_score": int(
                    adapt_trace.get("duration_match_score")
                    or speech_duration_match_score(actual_ms, slot_ms)
                ),
                "expansion_iterations": int(
                    adapt_trace.get("expansion_iterations") or 0
                ),
            }
        )
    return rows
