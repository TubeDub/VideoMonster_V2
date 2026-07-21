"""PSA4 — Slot Budget First (before TTS).

TTS is forbidden when predicted speech cannot fit the slot, or when
micro/fragment slots remain. Pipeline must normalize/merge/split first.
Flag: VM_FLAG_SLOT_BUDGET (default OFF → legacy allow-all).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.pipeline_integrity.slot_budget")

# Soft overflow still allowed into pause/gap; hard refuse above this ratio
HARD_OVERFLOW_RATIO = 1.35
CRITICAL_OVERFLOW_MS = 350


@dataclass
class SlotBudgetRow:
    segment_id: str
    index: int
    slot_ms: int
    predicted_tts_ms: int
    predicted_overflow_ms: int
    predicted_underflow_ms: int
    fill_pct: float
    speech_rate: float
    tts_allowed: bool
    reason: str = ""
    safety_margin_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "index": self.index,
            "slot_ms": self.slot_ms,
            "predicted_tts_ms": self.predicted_tts_ms,
            "predicted_overflow_ms": self.predicted_overflow_ms,
            "predicted_underflow_ms": self.predicted_underflow_ms,
            "fill_pct": self.fill_pct,
            "speech_rate": self.speech_rate,
            "tts_allowed": self.tts_allowed,
            "reason": self.reason,
            "safety_margin_ms": self.safety_margin_ms,
            "slot_strategy_reason": self.reason,
        }


@dataclass
class SlotBudgetReport:
    rows: list[SlotBudgetRow] = field(default_factory=list)
    tts_allowed: bool = True
    blocked: list[dict[str, Any]] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tts_allowed": self.tts_allowed,
            "blocked_count": len(self.blocked),
            "blocked": self.blocked,
            "remediation": self.remediation,
            "rows": [r.to_dict() for r in self.rows],
        }


def _predict_tts_ms(text: str, lang: str) -> int:
    t = str(text or "").strip()
    if not t:
        return 0
    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        ms = int(estimate_tts_duration_ms(t, (lang or "uk").split("-")[0]) or 0)
        if ms > 0:
            return ms
    except Exception:
        pass
    cps = 13.5 if (lang or "").startswith("uk") else 14.5
    return max(1, int(len(t) / cps * 1000))


def _seg_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("plain_text")
        or seg.get("text_for_tts")
        or seg.get("final_text")
        or seg.get("translation_text")
        or seg.get("text")
        or ""
    ).strip()


def _slot_ms(seg: dict[str, Any], timing_map: list[Any], idx: int) -> int:
    slot = int(seg.get("slot_ms") or 0)
    if slot > 0:
        return slot
    if idx < len(timing_map or []):
        item = timing_map[idx]
        if isinstance(item, dict):
            return max(0, int(item.get("end", 0)) - int(item.get("start", 0)))
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            return max(0, int(item[1]) - int(item[0]))
    s = int(seg.get("start_ms") or 0)
    e = int(seg.get("end_ms") or 0)
    return max(0, e - s)


def compute_slot_budgets(
    segments_data: list[dict[str, Any]],
    timing_map: list[Any] | None = None,
    *,
    tgt_lang: str = "uk",
) -> SlotBudgetReport:
    from engines.pipeline_integrity.v2_gates import slot_budget_enabled

    report = SlotBudgetReport()
    if not slot_budget_enabled():
        report.tts_allowed = True
        return report

    tm = list(timing_map or [])
    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        text = _seg_text(seg)
        slot = _slot_ms(seg, tm, idx)
        pred = _predict_tts_ms(text, tgt_lang) if text else 0
        overflow = max(0, pred - slot) if slot > 0 and pred > 0 else 0
        underflow = max(0, slot - pred) if slot > 0 and pred > 0 else 0
        fill = round((pred / slot) * 100.0, 1) if slot > 0 and pred > 0 else 0.0
        rate = round(len(text) / (slot / 1000.0), 2) if slot > 0 and text else 0.0
        margin = max(0, slot - pred) if slot > 0 else 0
        allowed = True
        reason = "ok"
        # PSA4: micro/fragment slots must not reach TTS
        try:
            from engines.pipeline_integrity.segment_normalizer import (
                is_micro_or_fragment,
            )

            if text and is_micro_or_fragment(text, slot):
                allowed = False
                reason = "micro_slot_unmerged"
        except Exception:
            if slot > 0 and slot < 850 and text:
                allowed = False
                reason = "micro_slot_unmerged"

        if allowed and (slot <= 0 or pred <= 0):
            allowed = False
            reason = "budget_uncomputable"
        elif allowed and (
            pred > int(slot * HARD_OVERFLOW_RATIO)
            or overflow > max(CRITICAL_OVERFLOW_MS * 3, int(slot * 0.35))
        ):
            allowed = False
            reason = "predicted_overflow_hard"
        elif allowed and overflow > CRITICAL_OVERFLOW_MS:
            # Soft block — remediation expected before TTS
            allowed = False
            reason = "predicted_overflow_critical"

        sid = str(seg.get("segment_id") or seg.get("segment_uuid") or idx)
        row = SlotBudgetRow(
            segment_id=sid,
            index=idx,
            slot_ms=slot,
            predicted_tts_ms=pred,
            predicted_overflow_ms=overflow,
            predicted_underflow_ms=underflow,
            fill_pct=fill,
            speech_rate=rate,
            tts_allowed=allowed,
            reason=reason,
            safety_margin_ms=margin,
        )
        report.rows.append(row)
        # Stamp on segment for Review / diagnostics
        seg["slot_budget"] = row.to_dict()
        seg["predicted_tts_ms"] = pred
        seg["predicted_overflow_ms"] = overflow
        seg["safety_margin_ms"] = margin
        seg["slot_strategy_reason"] = reason
        if not allowed:
            report.blocked.append(row.to_dict())

    report.tts_allowed = len(report.blocked) == 0
    if not report.tts_allowed:
        report.remediation = [
            "merge_micro_or_neighbors",
            "split_long_slot",
            "semantic_shortening",
            "retranslate_affected",
        ]
        logger.warning(
            "[SlotBudget] TTS blocked for %d segment(s)",
            len(report.blocked),
        )
    return report


def enforce_slot_budget_or_raise(
    segments_data: list[dict[str, Any]],
    timing_map: list[Any] | None = None,
    *,
    tgt_lang: str = "uk",
    task_info: dict[str, Any] | None = None,
    hard_raise: bool = False,
) -> SlotBudgetReport:
    """
    Compute budgets. If blocked and hard_raise — raise PipelineValidationError.
    Otherwise mark segments for remediation / manual_review.
    """
    report = compute_slot_budgets(
        segments_data, timing_map, tgt_lang=tgt_lang
    )
    if task_info is not None:
        task_info["slot_budget_report"] = report.to_dict()

    if report.tts_allowed:
        return report

    for row in report.blocked:
        idx = int(row["index"])
        if 0 <= idx < len(segments_data):
            seg = segments_data[idx]
            seg["needs_manual_review"] = True
            seg["slot_budget_blocked"] = True
            seg["tts_gate"] = "blocked_slot_budget"

    if hard_raise:
        from engines.pipeline_integrity.exceptions import PipelineValidationError

        raise PipelineValidationError(
            f"Slot Budget First: TTS forbidden for {len(report.blocked)} segment(s)",
            stage="slot_budget",
            details={"blocked": report.blocked[:20]},
        )
    return report


def segment_tts_allowed(seg: dict[str, Any]) -> bool:
    """Per-row TTS gate after SlotBudgetFirst."""
    if not isinstance(seg, dict):
        return False
    if seg.get("merged_into") is not None or seg.get("archived"):
        return False
    if seg.get("slot_budget_blocked") or seg.get("tts_gate") == "blocked_slot_budget":
        return False
    budget = seg.get("slot_budget")
    if isinstance(budget, dict) and budget.get("tts_allowed") is False:
        return False
    return True


def prepare_slot_budget_before_tts(
    segments_data: list[dict[str, Any]],
    timing_map: list[Any] | None = None,
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    task_info: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[Any], SlotBudgetReport]:
    """PSA4 orchestration: normalize/merge first, then budget; never TTS hard-blocked rows.

    Returns (segments, timing, report). When flags OFF → passthrough + allow-all.
    """
    from engines.pipeline_integrity.v2_gates import (
        segment_normalizer_enabled,
        slot_budget_enabled,
    )

    segs = list(segments_data or [])
    tm: list[Any] = list(timing_map or [])

    if segment_normalizer_enabled():
        try:
            from engines.pipeline_integrity.segment_normalizer import (
                normalize_segments_data,
            )

            segs, tm, norm_rep = normalize_segments_data(
                segs,
                tm,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                task_info=task_info,
            )
            if task_info is not None:
                task_info["segment_normalizer_pre_tts"] = norm_rep
        except Exception as exc:
            logger.warning("[SlotBudget] pre-TTS normalize skipped: %s", exc)

    report = enforce_slot_budget_or_raise(
        segs,
        tm,
        tgt_lang=tgt_lang,
        task_info=task_info,
        hard_raise=False,
    )

    # One remediation pass if still blocked and normalizer is on
    if (
        slot_budget_enabled()
        and not report.tts_allowed
        and segment_normalizer_enabled()
    ):
        try:
            from engines.pipeline_integrity.segment_normalizer import (
                normalize_segments_data,
            )

            segs, tm, norm_rep2 = normalize_segments_data(
                segs,
                tm,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                task_info=task_info,
            )
            if task_info is not None:
                task_info["segment_normalizer_remediation"] = norm_rep2
            report = enforce_slot_budget_or_raise(
                segs,
                tm,
                tgt_lang=tgt_lang,
                task_info=task_info,
                hard_raise=False,
            )
        except Exception as exc:
            logger.warning("[SlotBudget] remediation normalize skipped: %s", exc)

    if task_info is not None:
        task_info["slot_budget_ok"] = bool(report.tts_allowed)
        task_info["slot_budget_report"] = report.to_dict()

    return segs, tm, report
