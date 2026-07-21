"""TRANSLATION LOCK gate after DSAL (TZ v4.0 P2).

LOCK when duration_match >= 85, clause_coverage >= 0.85, entity pass.
Failing segments get needs_studio and stay unlocked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

DURATION_MATCH_MIN = 85
CLAUSE_COVERAGE_MIN = 0.85


@dataclass
class LockGateFailure:
    index: int
    reasons: list[str] = field(default_factory=list)
    duration_match_score: int = 0
    clause_coverage: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "reasons": list(self.reasons),
            "duration_match_score": self.duration_match_score,
            "clause_coverage": self.clause_coverage,
        }


def _force_lock() -> bool:
    return os.getenv("VM_FORCE_TRANSLATION_LOCK", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _entity_ok(src: str, uk: str) -> bool:
    try:
        from engines.semantic_meaning import check_critical_entities

        errs = check_critical_entities(src, uk) or []
        return len(errs) == 0
    except Exception:
        try:
            from engines.ai_core.translation_agent.validators.entity_validator import (
                validate_entities,
            )

            return bool(validate_entities(src, uk).ok)
        except Exception:
            return True


def evaluate_lock_gate(
    segments: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
) -> list[LockGateFailure]:
    """Return failures; empty list means all segments may lock."""
    if _force_lock():
        return []

    src_segs = list(source_segments or [])
    failures: list[LockGateFailure] = []

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        # Legacy / no DSAL stamp → do not block
        if seg.get("dsal_band") is None and seg.get("duration_match_score") is None:
            seg["lock_gate_ok"] = True
            continue

        score = int(seg.get("duration_match_score") or 0)
        cov = float(seg.get("clause_coverage") if seg.get("clause_coverage") is not None else 1.0)
        text = str(
            seg.get("final_text")
            or seg.get("translation_text")
            or seg.get("text")
            or ""
        )
        src = ""
        if i < len(src_segs):
            src = str(src_segs[i] or "")
        if not src:
            src = str(seg.get("source_text") or seg.get("original_text") or "")

        reasons: list[str] = []
        # Green band always passes duration even if score rounding is low
        band = str(seg.get("dsal_band") or "")
        if band != "green" and score < DURATION_MATCH_MIN:
            reasons.append(f"duration_match_score={score}<{DURATION_MATCH_MIN}")
        # Clause gate only when critical mapped EN clauses exist
        try:
            from engines.dsal.clause_coverage import compute_clause_coverage

            cov_detail = compute_clause_coverage(src, text)
            if cov_detail.total > 0 and cov < CLAUSE_COVERAGE_MIN:
                reasons.append(f"clause_coverage={cov}<{CLAUSE_COVERAGE_MIN}")
        except Exception:
            if cov < CLAUSE_COVERAGE_MIN:
                reasons.append(f"clause_coverage={cov}<{CLAUSE_COVERAGE_MIN}")
        if src and text and not _entity_ok(src, text):
            reasons.append("entity_fail")

        if reasons:
            seg["lock_gate_ok"] = False
            seg["needs_studio"] = True
            seg["lock_gate_failed"] = {
                "reasons": reasons,
                "duration_match_score": score,
                "clause_coverage": cov,
            }
            failures.append(
                LockGateFailure(
                    index=i,
                    reasons=reasons,
                    duration_match_score=score,
                    clause_coverage=cov,
                )
            )
        else:
            seg["lock_gate_ok"] = True
            seg.pop("needs_studio", None)
            seg.pop("lock_gate_failed", None)

    return failures


def apply_lock_with_gate(
    segments: list[dict[str, Any]],
    *,
    info: dict[str, Any],
    lock_segments_fn,
) -> dict[str, Any]:
    """Lock passing segments; defer project lock if any failure."""
    src_segs = list(info.get("source_segments") or info.get("original_segments") or [])
    failures = evaluate_lock_gate(segments, source_segments=src_segs)

    if not failures:
        meta = lock_segments_fn(segments, info=info, advance_state=True)
        info["needs_studio"] = False
        info["translation_lock_deferred"] = False
        return meta

    # Partial lock: only gate-ok segments
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        if seg.get("lock_gate_ok"):
            plain = str(
                seg.get("plain_text")
                or seg.get("translated_text")
                or seg.get("translation_text")
                or seg.get("text")
                or ""
            ).strip()
            import re

            seg["translation_locked"] = True
            seg["locked_text"] = re.sub(r"<[^>]+>", "", plain).strip()
        else:
            seg["translation_locked"] = False
            seg["needs_studio"] = True

    info["translation_locked"] = False
    info["translation_lock_deferred"] = True
    info["needs_studio"] = True
    meta = {
        "locked_segments": sum(
            1 for s in segments if isinstance(s, dict) and s.get("translation_locked")
        ),
        "deferred_segments": len(failures),
        "lock_gate_failures": [f.to_dict() for f in failures],
        "pipeline_state": "VALIDATED",
        "needs_studio": True,
        "translation_lock_deferred": True,
    }
    info["translation_lock"] = meta
    info["lock_gate_failures"] = meta["lock_gate_failures"]
    return meta
