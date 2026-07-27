# -*- coding: utf-8 -*-
"""1 source segment = 1 translation = 1 TTS text (Happy Path anti-bleed).

After MT / batch / resegment: never leave one UK blob on N neighbors, and never
blindly redistribute by timing alone when source texts are available.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.translation_segment_parity")

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", str(text or "").strip()).lower()


def _similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        return len(shorter) / max(1, len(longer))
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def split_translation_by_sources(
    combined: str,
    source_texts: list[str],
) -> list[str]:
    """Split one MT string back onto N source segments (char-proportional words)."""
    n = len(source_texts)
    if n <= 0:
        return []
    combined = _WS.sub(" ", str(combined or "").strip())
    if n == 1:
        return [combined]
    if not combined:
        return [""] * n

    # Prefer blank-line / sentence delimiter when counts match.
    parts = [p.strip() for p in re.split(r"\n\s*\n", combined) if p.strip()]
    if len(parts) == n:
        return parts

    try:
        from engines.pipeline_orchestrator.translation_batch import (
            TranslationBatch,
            split_batch_translation,
        )

        batch = TranslationBatch(
            batch_id=-1,
            segment_indices=list(range(n)),
            source_texts=[str(s or "") for s in source_texts],
        )
        mapping = split_batch_translation(batch, combined)
        out = [str(mapping.get(i, "") or "").strip() for i in range(n)]
        if sum(1 for x in out if x) >= max(1, n - 1):
            return out
    except Exception as exc:
        logger.debug("split_batch_translation fallback: %s", exc)

    src_lens = [max(1, len(str(s or "").strip())) for s in source_texts]
    total = sum(src_lens) or n
    words = combined.split()
    if not words:
        return [""] * n
    pos = 0
    out: list[str] = []
    for i, slen in enumerate(src_lens):
        if i == n - 1:
            out.append(" ".join(words[pos:]).strip())
            break
        n_words = max(1, round(len(words) * slen / total))
        n_words = min(n_words, max(1, len(words) - pos - (n - i - 1)))
        out.append(" ".join(words[pos : pos + n_words]).strip())
        pos += n_words
    while len(out) < n:
        out.append("")
    return out[:n]


def repair_length_imbalance_pairs(
    sources: list[str],
    translations: list[str],
) -> list[str]:
    """Re-split when left UK is huge vs short EN and right is empty/wrong/tiny.

    Classic post-resegment bleed: full paragraph stays on slot N, slot N+1 keeps
    a stray short phrase (or empty) while its EN owns the continuation.
    """
    n = len(translations)
    out = [str(t or "").strip() for t in translations]
    sources = [str(s or "").strip() for s in (sources or [])]
    while len(sources) < n:
        sources.append("")

    for i in range(max(0, n - 1)):
        src_a, src_b = sources[i], sources[i + 1]
        tr_a, tr_b = out[i], out[i + 1]
        if not src_a or not src_b or not tr_a:
            continue
        left_heavy = len(tr_a) >= max(90, int(len(src_a) * 2.0))
        right_light = (not tr_b) or len(tr_b) <= max(40, int(len(src_b) * 0.45))
        size_flip = len(src_b) >= len(src_a) and len(tr_a) > max(1, len(tr_b)) * 2.5
        if not (left_heavy and (right_light or size_flip)):
            continue
        blob = tr_a
        if tr_b and len(tr_b) > 24 and _similarity(tr_a, tr_b) >= 0.35:
            blob = (tr_a + " " + tr_b).strip()
        left, right = split_translation_by_sources(blob, [src_a, src_b])
        if left and right and (left != tr_a or right != tr_b):
            logger.info(
                "parity: length-imbalance repair #%d/#%d (%d+%d → %d+%d)",
                i,
                i + 1,
                len(tr_a),
                len(tr_b),
                len(left),
                len(right),
            )
            out[i], out[i + 1] = left, right
    return out


def detect_translation_bleed(
    sources: list[str],
    translations: list[str],
    *,
    similarity_threshold: float = 0.72,
) -> list[bool]:
    """Per-index flag: text nearly equal/contained in a neighbor."""
    n = len(translations)
    flags = [False] * n
    for i in range(n):
        cur = str(translations[i] or "").strip()
        src_i = str(sources[i] if i < len(sources) else "") or ""
        # Length-imbalance alone is a bleed signal.
        if (
            i + 1 < n
            and src_i
            and len(cur) >= max(90, int(len(src_i) * 2.0))
        ):
            src_j = str(sources[i + 1] if i + 1 < len(sources) else "") or ""
            other = str(translations[i + 1] or "").strip()
            if src_j and (
                not other or len(other) <= max(40, int(len(src_j) * 0.45))
            ):
                flags[i] = True
                flags[i + 1] = True
        if len(cur) < 24:
            continue
        for j in (i - 1, i + 1):
            if j < 0 or j >= n:
                continue
            other = str(translations[j] or "").strip()
            if not other:
                continue
            if _similarity(cur, other) >= similarity_threshold:
                flags[i] = True
                break
            src_j = str(sources[j] if j < len(sources) else "") or ""
            if (
                j == i + 1
                and src_i
                and src_j
                and len(cur) > max(80, int(len(src_i) * 2.2))
                and (
                    len(other) < max(40, int(len(src_j) * 0.45))
                    or any(
                        tok in _norm(cur)
                        for tok in _norm(src_j).split()[:8]
                        if len(tok) >= 5
                    )
                )
            ):
                flags[i] = True
                break
    return flags


def enforce_one_to_one_translations(
    sources: list[str],
    translations: list[str],
    *,
    timing_map: list[Any] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Guarantee len match + debleed adjacent copies. Prefer source-aware splits.

    Does **not** redistribute meaning by timing alone when counts already match.
    """
    src = [str(s or "").strip() for s in (sources or [])]
    tr = [str(t or "").strip() for t in (translations or [])]
    audits: list[dict[str, Any]] = []

    # Length parity vs sources (authoritative for meaning).
    if src and len(tr) != len(src):
        logger.warning(
            "translation parity: translations=%d sources=%d — source-aware remap",
            len(tr),
            len(src),
        )
        if len(tr) < len(src):
            combined = " ".join(x for x in tr if x).strip()
            tr = split_translation_by_sources(combined, src)
        else:
            while len(tr) > len(src) and len(tr) >= 2:
                tr[-2] = (tr[-2] + " " + tr[-1]).strip()
                tr.pop()
            if len(tr) < len(src):
                tr.extend([""] * (len(src) - len(tr)))
            tr = tr[: len(src)]
        audits.append({"action": "remap_to_source_count", "n": len(src)})

    tm = list(timing_map or [])
    if tm and src and len(tm) != len(src):
        logger.warning(
            "translation parity: timing_map=%d sources=%d (keeping source count)",
            len(tm),
            len(src),
        )

    try:
        from engines.translation_naturalizer import debleed_adjacent_batch_copies

        before = list(tr)
        tr = debleed_adjacent_batch_copies(src if src else [""] * len(tr), tr)
        for i, (a, b) in enumerate(zip(before, tr)):
            if a != b:
                audits.append(
                    {
                        "index": i,
                        "action": "debleed",
                        "before_len": len(a),
                        "after_len": len(b),
                    }
                )
    except Exception as exc:
        logger.debug("debleed skipped: %s", exc)

    before_imb = list(tr)
    tr = repair_length_imbalance_pairs(src if src else [""] * len(tr), tr)
    for i, (a, b) in enumerate(zip(before_imb, tr)):
        if a != b:
            audits.append({"index": i, "action": "length_imbalance_repair"})

    for i in range(max(0, len(tr) - 1)):
        a, b = tr[i], tr[i + 1]
        if not a or not b:
            continue
        if a == b or (len(a) > 40 and (a in b or b in a)):
            blob = a if len(a) >= len(b) else b
            pair_src = [
                src[i] if i < len(src) else "",
                src[i + 1] if i + 1 < len(src) else "",
            ]
            left, right = split_translation_by_sources(blob, pair_src)
            if left != right or left != blob:
                tr[i], tr[i + 1] = left, right
                audits.append({"index": i, "action": "force_pair_split"})

    bleed = detect_translation_bleed(src if src else [""] * len(tr), tr)
    for i, flag in enumerate(bleed):
        audits.append({"index": i, "translation_bleed": bool(flag)})

    return tr, audits


def stamp_segment_translation_audit(
    seg: dict[str, Any],
    *,
    original: str,
    translated: str,
    tts_text: str,
    translation_bleed: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write per-segment diagnostics required by TZ Stage 3."""
    if not isinstance(seg, dict):
        return
    row = {
        "original": str(original or "")[:500],
        "translated_for_this_segment": str(translated or "")[:500],
        "tts_text": str(tts_text or "")[:500],
        "translation_bleed": bool(translation_bleed),
        "original_len": len(str(original or "")),
        "fitted_len": len(str(tts_text or translated or "")),
    }
    if extra:
        row.update(extra)
    seg["translation_parity"] = row
    seg["translation_bleed"] = bool(translation_bleed)
