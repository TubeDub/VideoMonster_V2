"""Soft-compress target dub lines that overflow tight TTS slots."""

from __future__ import annotations

import re


def soft_compress_for_slot(
    text: str,
    *,
    slot_ms: int,
    target_lang: str = "uk",
    chars_per_sec: float | None = None,
) -> str:
    """Light filler compress when text is too long for the slot.

    Never turns commas into periods (that created «їхав. Джордж») and never
    chops trailing clauses — only drop mild fillers that keep meaning.
    """
    t = " ".join(str(text or "").split()).strip()
    if not t or slot_ms <= 0:
        return t

    cps = chars_per_sec
    if cps is None:
        lang = str(target_lang or "uk").split("-")[0].lower()
        cps = 12.0 if lang in ("uk", "ru", "be") else 14.0

    budget = max(8, int((slot_ms / 1000.0) * cps))
    if len(t) <= budget + 8:
        return t

    out = t
    # Soften em-dash pauses only — do NOT replace ", " with ". "
    out = out.replace(" — ", ", ").replace(" – ", ", ")
    out = re.sub(r"\s{2,}", " ", out).strip()

    # Only drop pure hesitation fillers — never «насправді/дійсно/просто»
    # (those are meaning/discourse words; stripping them caused Review
    # tts_truncation vs Final). Language-gated so UK fillers never touch RU.
    lang = str(target_lang or "uk").split("-")[0].lower()
    if lang == "uk":
        fillers = (r"\bну\b", r"\bвласне\b", r"\bскажімо\b")
    elif lang == "ru":
        fillers = (r"\bну\b", r"\bкак\s+бы\b", r"\bскажем\b")
    else:
        fillers = (r"\buh+\b", r"\bum+\b")
    if len(out) > budget:
        for pat in fillers:
            out2 = re.sub(pat, "", out, flags=re.I)
            out2 = " ".join(out2.split())
            if out2:
                out = out2
            if len(out) <= budget:
                break

    # Refuse destructive clause chop — keep full meaning; caller may expand slot.
    try:
        from engines.semantic_meaning import is_truncated_adaptation

        if is_truncated_adaptation(t, out):
            return t
    except Exception:
        pass

    # If still massively over budget after filler drop, keep original
    # (Meaning Fit / merge / video-adapt handle overflow — not mid-clause cut).
    if len(out) > budget + 40 and len(out) < int(len(t) * 0.85):
        return out
    if len(out) > budget + 40:
        return t
    return out.strip()
