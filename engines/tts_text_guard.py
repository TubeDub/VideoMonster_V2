# -*- coding: utf-8 -*-
"""Pre-TTS guards: phrase-loop deflate, neighbor-bleed restore, text↔tts sync.

Integrates into existing stamp / integrity paths — not a parallel validator.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.tts_text_guard")

_TEXT_KEYS = (
    "text",
    "plain_text",
    "final_text",
    "text_for_tts",
    "tts_text",
    "voice_input",
    "translation_text",
    "grammar_text",
    "timing_text",
)


def _norm(s: str) -> str:
    t = str(s or "").lower()
    t = re.sub(r"[\u0300-\u036f]", "", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return " ".join(t.split())


def _token_jaccard(a: str, b: str) -> float:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def _canonical_own(seg: dict[str, Any]) -> str:
    """Best owned text for this segment (prefer final/naturalized over corrupt tts)."""
    try:
        from engines.translation_validation import is_shared_mt_blob_reclaim
    except Exception:
        is_shared_mt_blob_reclaim = None  # type: ignore[assignment]

    final = str(seg.get("approved_text") or seg.get("final_text") or "").strip()
    translated = str(
        seg.get("translated_text") or seg.get("naturalized_text") or ""
    ).strip()
    semantic = str(
        seg.get("semantic_text") or seg.get("semantic_engine_text") or ""
    ).strip()
    for owned in (final, translated):
        if not owned:
            continue
        if (
            semantic
            and is_shared_mt_blob_reclaim is not None
            and is_shared_mt_blob_reclaim(owned, semantic)
        ):
            return owned
        return owned
    for key in (
        "semantic_text",
        "plain_text",
        "text",
        "translation_text",
    ):
        val = str(seg.get(key) or "").strip()
        if val:
            return val
    return ""


_BARE_INF_AFTER_NAME = re.compile(
    r"\b(молодший|він|вона|батько|син)\s+(відчути|помітити|сказати|зрозуміти)\b",
    re.I,
)


def heal_bare_infinitive(text: str) -> str:
    """Repair already-broken «молодший відчути» → «молодший відчув»."""
    from engines.semantic_meaning import _uk_infinitive_to_past

    def _sub(m: re.Match[str]) -> str:
        return f"{m.group(1)} {_uk_infinitive_to_past(m.group(2))}"

    return _BARE_INF_AFTER_NAME.sub(_sub, text)


# Back-compat alias
_heal_bare_infinitive = heal_bare_infinitive


def prepare_segment_text_for_tts(seg: dict[str, Any]) -> dict[str, Any]:
    """Deflate loops + stamp consistent TTS fields on one segment."""
    report: dict[str, Any] = {"changed": False, "actions": []}
    if not isinstance(seg, dict):
        return report
    text = _canonical_own(seg)
    if not text:
        return report

    try:
        from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop

        if has_phrase_loop(text):
            fixed = deflate_phrase_loop(text)
            if fixed and fixed != text and not has_phrase_loop(fixed):
                text = fixed
                report["actions"].append("deflate_phrase_loop")
                report["changed"] = True
    except Exception:
        pass

    lang = str(
        seg.get("target_lang") or seg.get("tgt_lang") or ""
    ).split("-")[0].lower()
    if not lang:
        if re.search(r"[іІїЇєЄґҐ]", text):
            lang = "uk"
        elif re.search(r"[а-яА-ЯёЁ]", text):
            lang = "ru"
        else:
            lang = "uk"

    # Compact phrases safely (no bare infinitive) — language-gated.
    try:
        from engines.semantic_meaning import apply_compact_phrases

        compacted = apply_compact_phrases(text, target_lang=lang)
        if compacted and compacted != text:
            if lang == "uk" and re.search(
                r"\b(молодший|він)\s+відчути\b", compacted, re.I
            ):
                report["actions"].append("reject_bare_infinitive_compact")
            else:
                text = compacted
                report["actions"].append("compact_phrases")
                report["changed"] = True
    except Exception:
        pass

    # Bare-infinitive heal is UK-specific (молодший відчути).
    if lang == "uk":
        healed_inf = _heal_bare_infinitive(text)
        if healed_inf != text:
            text = healed_inf
            report["actions"].append("heal_bare_infinitive")
            report["changed"] = True

    for key in _TEXT_KEYS:
        if key in seg or key in ("text", "plain_text", "tts_text", "text_for_tts"):
            if str(seg.get(key) or "") != text:
                seg[key] = text
                report["changed"] = True
    seg["tts_text"] = text
    seg["text_for_tts"] = text
    seg["text"] = text
    seg["plain_text"] = text
    return report


def repair_neighbor_bleed(
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
) -> dict[str, Any]:
    """If seg TTS text matches a neighbor better than itself, restore own final.

    Also slices shared raw_translation blobs that were copied onto both slots.
    """
    healed: list[int] = []
    actions: list[dict[str, Any]] = []
    n = len(segments_data or [])
    own_texts = [_canonical_own(s) if isinstance(s, dict) else "" for s in segments_data]

    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        spoken = str(
            seg.get("tts_text")
            or seg.get("text_for_tts")
            or seg.get("text")
            or seg.get("plain_text")
            or ""
        ).strip()
        own = own_texts[i]
        if not spoken:
            continue

        # Phrase loop always heal
        prep = prepare_segment_text_for_tts(seg)
        if prep.get("changed"):
            healed.append(i)
            actions.append({"index": i, **prep})
            spoken = str(seg.get("tts_text") or "")
            own = _canonical_own(seg)

        # Stale MT blob on TTS fields while Final/translated is the owned split.
        try:
            from engines.translation_validation import is_shared_mt_blob_reclaim

            owned_split = str(
                seg.get("approved_text")
                or seg.get("final_text")
                or seg.get("translated_text")
                or ""
            ).strip()
            blob_cand = str(
                seg.get("semantic_engine_text")
                or seg.get("semantic_text")
                or seg.get("raw_translation")
                or ""
            ).strip()
            if owned_split and (
                is_shared_mt_blob_reclaim(owned_split, spoken)
                or (
                    blob_cand
                    and is_shared_mt_blob_reclaim(owned_split, blob_cand)
                    and _norm(spoken) == _norm(blob_cand)
                )
            ):
                for key in _TEXT_KEYS:
                    seg[key] = owned_split
                seg["tts_text"] = owned_split
                seg["text_for_tts"] = owned_split
                seg["final_text"] = owned_split
                if blob_cand and is_shared_mt_blob_reclaim(owned_split, blob_cand):
                    seg["semantic_text"] = owned_split
                    seg["semantic_engine_text"] = owned_split
                own_texts[i] = owned_split
                healed.append(i)
                actions.append(
                    {
                        "index": i,
                        "action": "restore_owned_split_vs_mt_blob",
                        "preview": owned_split[:120],
                    }
                )
                continue
        except Exception:
            pass

        # Prefix bleed only (high precision): spoken starts with previous full text
        if i > 0 and own_texts[i - 1]:
            prev = own_texts[i - 1]
            prev_n = _norm(prev)
            spoken_n = _norm(spoken)
            if (
                len(prev) >= 40
                and spoken_n.startswith(prev_n[: min(48, len(prev_n))])
                and len(spoken) > len(prev) + 24
                and _token_jaccard(spoken, prev) > 0.45
            ):
                # Strip previous sentence prefix
                trimmed = spoken
                if spoken.startswith(prev):
                    trimmed = spoken[len(prev) :].lstrip(" .,—–-")
                else:
                    # Fuzzy: drop first len(prev.split()) tokens
                    toks = spoken.split()
                    cut = len(prev.split())
                    trimmed = " ".join(toks[cut:]).strip()
                if trimmed and len(trimmed) >= 20 and _token_jaccard(trimmed, prev) < 0.5:
                    for key in _TEXT_KEYS:
                        seg[key] = trimmed
                    seg["tts_text"] = trimmed
                    seg["text_for_tts"] = trimmed
                    healed.append(i)
                    actions.append(
                        {
                            "index": i,
                            "action": "strip_prev_prefix_bleed",
                            "preview": trimmed[:120],
                        }
                    )
                    continue

        # Shared blob across neighbors: source-aware split (not naive mid-cut).
        if own and n > 1:
            for j in (i + 1,):
                if not (0 <= j < n and own_texts[j]):
                    continue
                nb = own_texts[j]
                if _token_jaccard(own, nb) < 0.85 and own != nb:
                    continue
                blob = own if len(own) >= len(nb) else nb
                left = right = ""
                src_a = src_b = ""
                if source_segments and i < len(source_segments) and j < len(source_segments):
                    src_a = str(source_segments[min(i, j)] or "")
                    src_b = str(source_segments[max(i, j)] or "")
                    try:
                        from engines.translation_naturalizer import _split_uk_for_en_pair

                        left, right = _split_uk_for_en_pair(blob, src_a, src_b)
                    except Exception:
                        left = right = ""
                if not left or not right:
                    parts = [
                        p.strip()
                        for p in re.split(r"(?<=[.!?…])\s+", blob)
                        if p.strip()
                    ]
                    if len(parts) < 2:
                        continue
                    # Prefer first sentence left when EN_a is a complete short line.
                    if src_a and src_a.rstrip()[-1:] in ".!?…" and len(src_a.split()) <= 28:
                        left, right = parts[0], " ".join(parts[1:])
                    else:
                        mid = max(1, len(parts) // 2)
                        left = " ".join(parts[:mid]).strip()
                        right = " ".join(parts[mid:]).strip()
                if not left or not right:
                    continue
                lo, hi = (i, j) if i < j else (j, i)
                for idx, piece in ((lo, left), (hi, right)):
                    if not isinstance(segments_data[idx], dict):
                        continue
                    for key in _TEXT_KEYS:
                        segments_data[idx][key] = piece
                    segments_data[idx]["tts_text"] = piece
                    segments_data[idx]["text_for_tts"] = piece
                    segments_data[idx]["final_text"] = piece
                    own_texts[idx] = piece
                healed.extend([lo, hi])
                actions.append(
                    {
                        "index": i,
                        "action": "split_shared_blob",
                        "neighbor": j,
                        "left_preview": left[:80],
                        "right_preview": right[:80],
                    }
                )
                break

        # Neighbor ownership: spoken much closer to neighbor's DISTINCT text than own
        if own and n > 1:
            own_score = _token_jaccard(spoken, own)
            for j in (i - 1, i + 1):
                if not (0 <= j < n and own_texts[j]):
                    continue
                nb = own_texts[j]
                if _token_jaccard(own, nb) > 0.8:
                    continue
                nb_score = _token_jaccard(spoken, nb)
                if nb_score >= 0.72 and nb_score > own_score + 0.25:
                    restore = own
                    for key in _TEXT_KEYS:
                        seg[key] = restore
                    seg["tts_text"] = restore
                    seg["text_for_tts"] = restore
                    seg["bleed_repaired_from"] = j
                    healed.append(i)
                    actions.append(
                        {
                            "index": i,
                            "action": "restore_from_own_vs_neighbor",
                            "neighbor": j,
                            "score": round(nb_score, 3),
                            "preview": restore[:120],
                        }
                    )
                    break

    return {
        "healed_indices": sorted(set(healed)),
        "actions": actions,
        "healed": len(set(healed)),
    }


def sync_tts_fields_from_text(seg: dict[str, Any]) -> bool:
    """Force tts_text/text_for_tts to match canonical text (pre re-TTS)."""
    if not isinstance(seg, dict):
        return False
    canon = str(seg.get("text") or seg.get("plain_text") or "").strip()
    if not canon:
        return False
    changed = False
    for key in ("tts_text", "text_for_tts", "voice_input", "final_text"):
        if str(seg.get(key) or "").strip() != canon:
            seg[key] = canon
            changed = True
    return changed
