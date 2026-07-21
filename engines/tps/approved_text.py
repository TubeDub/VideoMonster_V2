"""Single Approved Text API (TPS3)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.tps.approved_text")


class ApprovedTextMutationError(RuntimeError):
    """Raised when code tries to rewrite approved/locked text."""


def get_approved_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("approved_text")
        or seg.get("voice_input")
        or seg.get("final_text")
        or seg.get("text")
        or ""
    ).strip()


def is_translation_locked(seg: dict[str, Any]) -> bool:
    return bool(
        seg.get("translation_locked")
        or seg.get("tps_locked")
        or (
            str(seg.get("tqe_status") or "").upper() == "PASS"
            and seg.get("approved_text")
        )
    )


def approve_segment(
    seg: dict[str, Any],
    text: str,
    *,
    tqe_status: str = "PASS",
    path: str = "fast",
    task_id: str = "",
    index: int = -1,
) -> str:
    """Stamp the single source of truth for Review / TTS / Scheduler."""
    final = str(text or "").strip()
    if not final:
        raise ValueError("cannot approve empty text")
    try:
        from engines.tps.owners import get_owner_registry

        get_owner_registry(task_id).claim(
            "final_approve", "TQE", segment_index=index
        )
        get_owner_registry(task_id).claim(
            "approved_text", "ApprovedTextAPI", segment_index=index
        )
    except Exception as exc:
        logger.debug("owner claim on approve: %s", exc)

    seg["approved_text"] = final
    seg["tqe_status"] = tqe_status
    seg["tps_path"] = path
    seg["translation_locked"] = True
    seg["tps_locked"] = True
    # Keep display/TTS fields in sync — no divergent copies
    for key in (
        "final_text",
        "voice_input",
        "text_for_tts",
        "plain_text",
        "translation_text",
        "text",
        "grammar_text",
        "timing_text",
        "semantic_text",
    ):
        seg[key] = final
    return final


def guard_post_pass_mutation(
    seg: dict[str, Any],
    *,
    new_text: str,
    allow_cosmetics: bool = False,
) -> str:
    """Block meaning-changing rewrites after TQE PASS.

    Cosmetics (SSML/stress) must not change word identity — caller strips markup
    before compare when allow_cosmetics=True.
    """
    if not is_translation_locked(seg):
        return new_text
    approved = get_approved_text(seg)
    candidate = str(new_text or "").strip()
    if allow_cosmetics:
        return candidate  # SSML/stress applied in TTS layer only
    if _normalize_words(candidate) != _normalize_words(approved):
        # Count attempt for metrics
        seg["approved_text_mutation_attempts"] = int(
            seg.get("approved_text_mutation_attempts") or 0
        ) + 1
        raise ApprovedTextMutationError(
            f"post-PASS rewrite blocked: approved={approved[:60]!r} new={candidate[:60]!r}"
        )
    return approved


def _normalize_words(text: str) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return " ".join(s.casefold().split())


def approved_texts_from_segments(segments: list[dict[str, Any]]) -> list[str]:
    return [get_approved_text(s if isinstance(s, dict) else {}) for s in segments]


def sync_audits_approved(info: dict[str, Any]) -> None:
    """Mirror approved_text into translation_audits for Review UI.

    TRH: also sync naturalized_text, route from tps_path, naturalizer flags.
    """
    try:
        from engines.trh import sync_audits_trh

        sync_audits_trh(info)
        return
    except Exception as exc:
        logger.debug("TRH audit sync fallback: %s", exc)

    segments = list(info.get("segments_data") or [])
    audits = list(info.get("translation_audits") or [])
    by_idx = {int(a.get("index", -1)): a for a in audits}
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        approved = get_approved_text(seg)
        if not approved:
            continue
        row = by_idx.get(i)
        if row is None:
            row = {"index": i}
            audits.append(row)
            by_idx[i] = row
        row["final_text"] = approved
        row["tts_text"] = approved
        row["approved_text"] = approved
        row["tqe_status"] = seg.get("tqe_status") or row.get("tqe_status")
        if not str(row.get("raw_translation") or "").strip():
            row["raw_translation"] = str(seg.get("translated_text") or "").strip()
        # Minimal TRH fields even on fallback
        nat = str(seg.get("naturalized_text") or "").strip()
        if nat:
            row["naturalized_text"] = nat
        path = str(seg.get("tps_path") or "")
        if path:
            row["route"] = path
            row["route_label"] = path
            row["tps_path"] = path
    info["translation_audits"] = audits
    info["segments_data"] = segments
