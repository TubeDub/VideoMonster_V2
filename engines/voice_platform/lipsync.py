"""P611 Phoneme + P612 Viseme + P613 Lip Sync 2.0 data."""

from __future__ import annotations

from typing import Any

from engines.voice_platform.types import LipSyncData, PhonemeSpec, VisemeSpec


def build_phoneme_specs(
    text: str,
    *,
    duration_ms: float = 1000.0,
    speech_rate: float = 1.0,
) -> list[PhonemeSpec]:
    """P611 — IPA / phonemes / duration / position per word."""
    from engines.semantic_v3.phoneme_viseme import analyze_word_phonemes

    words = (text or "").split()
    if not words:
        return []
    per = max(40.0, float(duration_ms) / len(words))
    out: list[PhonemeSpec] = []
    pos = 0
    for w in words:
        tokens = analyze_word_phonemes(w, duration_ms=int(per), speech_rate=speech_rate)
        for t in tokens:
            ipa = str(getattr(t, "ipa", "") or "")
            dur = float(getattr(t, "duration_ms", per / max(1, len(tokens))))
            stress = float(getattr(t, "stress", 0.0) or 0.0)
            out.append(
                PhonemeSpec(
                    ipa=ipa,
                    phoneme=ipa,
                    duration_ms=dur,
                    position=pos,
                    stress=stress,
                    word=w,
                )
            )
            pos += 1
    return out


def build_viseme_specs(phonemes: list[PhonemeSpec]) -> list[VisemeSpec]:
    """P612 — viseme + mouth geometry timing from phonemes."""
    from engines.semantic_v3.phoneme_viseme import PhonemeToken, phonemes_to_visemes

    tokens = [
        PhonemeToken(
            ipa=p.ipa or p.phoneme,
            duration_ms=p.duration_ms,
            stress=p.stress,
        )
        for p in phonemes
    ]
    if not tokens:
        return []
    try:
        vis = phonemes_to_visemes(tokens)
    except Exception:
        return _manual_visemes(phonemes)

    out: list[VisemeSpec] = []
    t = 0.0
    for i, v in enumerate(vis):
        dur = float(getattr(v, "timing_ms", None) or phonemes[i].duration_ms if i < len(phonemes) else 50)
        start = t
        end = t + max(10.0, dur)
        out.append(
            VisemeSpec(
                viseme=str(getattr(v, "viseme", "A") or "A"),
                mouth_open=float(getattr(v, "mouth_open", 0.4) or 0.4),
                mouth_close=float(getattr(v, "mouth_close", 0.2) or 0.2),
                jaw=float(getattr(v, "jaw_position", None) or getattr(v, "jaw", 0.3) or 0.3),
                lip_rounding=float(getattr(v, "lip_rounding", 0.2) or 0.2),
                start_ms=start,
                end_ms=end,
                phoneme=phonemes[i].phoneme if i < len(phonemes) else "",
            )
        )
        t = end
    return out


def _manual_visemes(phonemes: list[PhonemeSpec]) -> list[VisemeSpec]:
    vowel_map = {"a": "A", "e": "E", "i": "I", "o": "O", "u": "U", "y": "Y"}
    t = 0.0
    out: list[VisemeSpec] = []
    for p in phonemes:
        ch = (p.phoneme or p.ipa or "a")[:1].lower()
        vis = vowel_map.get(ch, "T")
        open_amt = 0.55 if vis in "AEIOU" else 0.25
        end = t + max(20.0, p.duration_ms)
        out.append(
            VisemeSpec(
                viseme=vis,
                mouth_open=open_amt,
                mouth_close=1.0 - open_amt,
                jaw=open_amt * 0.8,
                lip_rounding=0.5 if vis in "OU" else 0.2,
                start_ms=t,
                end_ms=end,
                phoneme=p.phoneme,
            )
        )
        t = end
    return out


def build_lipsync_data(
    speech_uuid: str,
    text: str,
    *,
    duration_ms: float = 1000.0,
    speech_rate: float = 1.0,
) -> LipSyncData:
    """P613 — Lip Sync 2.0 data package (no animation)."""
    phones = build_phoneme_specs(text, duration_ms=duration_ms, speech_rate=speech_rate)
    visemes = build_viseme_specs(phones)
    return LipSyncData(speech_uuid=speech_uuid, phonemes=phones, visemes=visemes)


def lipsync_from_speech_units(units: list[Any]) -> dict[str, dict]:
    """Batch Lip Sync 2.0 for Dub Engine speech units / dicts."""
    out: dict[str, dict] = {}
    for u in units:
        if isinstance(u, dict):
            suid = str(u.get("speech_uuid") or "")
            text = str(u.get("text") or "")
            dur = float(u.get("predicted_duration") or u.get("duration_ms") or 1000)
        else:
            suid = str(getattr(u, "speech_uuid", "") or "")
            text = str(getattr(u, "text", "") or "")
            dur = float(getattr(u, "predicted_duration", 1000) or 1000)
        if not suid:
            continue
        out[suid] = build_lipsync_data(suid, text, duration_ms=dur).to_dict()
    return out
