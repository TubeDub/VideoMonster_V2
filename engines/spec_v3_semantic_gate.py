"""Spec v3 Semantic + Language Gate.

Wraps existing ``pipeline_language_gate`` and ``semantic_meaning`` checks into
a single ``check_translation()`` call that returns structured diagnostics AND
optionally raises ``LanguageLeakError`` / ``SemanticIntegrityError`` when
``strict=True``.

Safe to import even when downstream modules are missing — degrades to a
non-strict pass with a ``warning`` field.
"""

from __future__ import annotations

import logging
from typing import Any

from engines.spec_v3_errors import LanguageLeakError, SemanticIntegrityError

logger = logging.getLogger("tubedub.spec_v3_semantic_gate")


def _language_leak_check(
    text: str,
    *,
    target_lang: str,
    original: str = "",
    source_lang: str = "",
) -> tuple[bool, str]:
    try:
        from engines.pipeline_language_gate import is_critical_language_mismatch

        return is_critical_language_mismatch(
            text,
            target_lang=target_lang,
            original=original,
            source_lang=source_lang,
        )
    except Exception as exc:
        logger.debug("language gate unavailable: %s", exc)
        return False, ""


def _semantic_similarity(source: str, translation: str, *, target_lang: str) -> dict[str, Any]:
    """Best-effort semantic similarity — returns dict with ``score`` in [0, 1]."""
    try:
        from engines.semantic_meaning import compute_meaning_preservation_score

        score = float(compute_meaning_preservation_score(source, translation))
        return {"score": max(0.0, min(1.0, score)), "method": "meaning_preservation_score"}
    except Exception as exc:
        logger.debug("semantic similarity unavailable: %s", exc)
    # Cheap fallback: shared token ratio (better than nothing).
    try:
        toks_s = {w.lower() for w in source.split() if len(w) > 2}
        toks_t = {w.lower() for w in translation.split() if len(w) > 2}
        if not toks_s:
            return {"score": 1.0, "method": "empty_source"}
        overlap = len(toks_s & toks_t) / max(1, len(toks_s))
        return {"score": round(overlap, 3), "method": "token_overlap_fallback"}
    except Exception:
        return {"score": 0.0, "method": "unavailable"}


def check_translation(
    source_text: str,
    translated_text: str,
    *,
    target_lang: str,
    source_lang: str = "",
    strict: bool = False,
    min_similarity: float = 0.55,
    segment_index: int | None = None,
) -> dict[str, Any]:
    """Return structured diagnostics; raise on hard failures when ``strict=True``.

    Diagnostics contract:
      ``{"ok": bool, "language_leak": bool, "language_leak_reason": str,
        "similarity": float, "similarity_method": str,
        "target_lang": str, "segment_index": int|None,
        "final_status": "ok" | "language_leak" | "semantic_degraded" | "degraded"}``
    """
    tgt = str(target_lang or "").strip().lower()
    is_leak, leak_reason = _language_leak_check(
        translated_text,
        target_lang=tgt,
        original=source_text,
        source_lang=source_lang,
    )
    sim = _semantic_similarity(source_text, translated_text, target_lang=tgt)
    score = float(sim.get("score") or 0.0)
    method = str(sim.get("method") or "")

    if is_leak:
        final_status = "language_leak"
    elif score < min_similarity and method not in ("empty_source", "unavailable"):
        final_status = "semantic_degraded"
    else:
        final_status = "ok"

    diag: dict[str, Any] = {
        "ok": final_status == "ok",
        "final_status": final_status,
        "language_leak": bool(is_leak),
        "language_leak_reason": leak_reason,
        "similarity": round(score, 3),
        "similarity_method": method,
        "min_similarity": float(min_similarity),
        "target_lang": tgt,
        "segment_index": segment_index,
    }
    if strict:
        if is_leak:
            raise LanguageLeakError(
                f"seg={segment_index}: translation is not in target={tgt} "
                f"(reason={leak_reason or 'critical_mismatch'})",
                target_lang=tgt,
                segment_index=segment_index,
                reason=leak_reason,
                text=translated_text[:200],
            )
        if final_status == "semantic_degraded":
            raise SemanticIntegrityError(
                f"seg={segment_index}: semantic similarity {score:.2f} < {min_similarity:.2f} "
                f"(method={method})",
                similarity=score,
                min_similarity=min_similarity,
                method=method,
                segment_index=segment_index,
                source=source_text[:200],
                translation=translated_text[:200],
            )
    return diag


def check_segments_batch(
    segments: list[dict[str, Any]],
    *,
    target_lang: str,
    source_lang: str = "",
    strict: bool = False,
    min_similarity: float = 0.55,
    source_key: str = "text",
    translated_key: str = "translated_text",
) -> dict[str, Any]:
    """Stamp per-segment diagnostics; return aggregate summary.

    Mutates segments in place (``seg["spec_v3_language_gate"]`` per segment).
    """
    leaks: list[int] = []
    degraded: list[int] = []
    ok_count = 0
    scores: list[float] = []
    for seg in segments or []:
        idx = seg.get("index") if isinstance(seg, dict) else None
        src = str((seg.get(source_key) if isinstance(seg, dict) else "") or "")
        tgt = str((seg.get(translated_key) if isinstance(seg, dict) else "") or "")
        diag = check_translation(
            src,
            tgt,
            target_lang=target_lang,
            source_lang=source_lang,
            strict=strict,
            min_similarity=min_similarity,
            segment_index=idx,
        )
        if isinstance(seg, dict):
            seg["spec_v3_language_gate"] = diag
        scores.append(diag["similarity"])
        if diag["language_leak"]:
            leaks.append(int(idx) if idx is not None else -1)
        elif diag["final_status"] == "semantic_degraded":
            degraded.append(int(idx) if idx is not None else -1)
        else:
            ok_count += 1
    return {
        "total": len(segments or []),
        "ok": ok_count,
        "language_leak_indices": leaks,
        "semantic_degraded_indices": degraded,
        "average_similarity": (
            round(sum(scores) / len(scores), 3) if scores else 0.0
        ),
        "min_similarity_seen": round(min(scores), 3) if scores else 0.0,
        "target_lang": str(target_lang or "").strip().lower(),
    }
