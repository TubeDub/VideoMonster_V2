"""P36 Phoneme Engine + P37 Viseme Engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Deterministic grapheme→coarse phoneme map (EN/UK approximation; not full IPA NLP)
_G2P: dict[str, str] = {
    "a": "a", "e": "e", "i": "i", "o": "o", "u": "u", "y": "i",
    "а": "a", "е": "e", "є": "je", "и": "ɪ", "і": "i", "ї": "ji",
    "о": "o", "у": "u", "ю": "ju", "я": "ja", "ё": "jo",
    "b": "b", "c": "k", "d": "d", "f": "f", "g": "g", "h": "h",
    "j": "dʒ", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p",
    "q": "k", "r": "r", "s": "s", "t": "t", "v": "v", "w": "w",
    "x": "ks", "z": "z",
    "б": "b", "в": "v", "г": "ɦ", "ґ": "g", "д": "d", "ж": "ʒ",
    "з": "z", "й": "j", "к": "k", "л": "l", "м": "m", "н": "n",
    "п": "p", "р": "r", "с": "s", "т": "t", "ф": "f", "х": "x",
    "ц": "ts", "ч": "tʃ", "ш": "ʃ", "щ": "ʃtʃ", "ь": "", "ъ": "",
}

# Phoneme → viseme class (Disney/Amazon-style coarse set)
_PHONE_TO_VISEME: dict[str, str] = {
    "a": "A", "e": "E", "i": "I", "ɪ": "I", "o": "O", "u": "U",
    "ja": "A", "je": "E", "ji": "I", "jo": "O", "ju": "U",
    "p": "P", "b": "P", "m": "P",
    "f": "F", "v": "F",
    "t": "T", "d": "T", "n": "T", "l": "L", "r": "R",
    "s": "S", "z": "S", "ʃ": "S", "ʒ": "S", "ts": "S", "tʃ": "S", "ʃtʃ": "S",
    "k": "K", "g": "K", "ɦ": "K", "x": "K",
    "w": "W", "j": "Y", "dʒ": "S", "h": "K", "ks": "K",
}

_VISEME_MOUTH: dict[str, dict[str, float]] = {
    "A": {"mouth_open": 0.8, "mouth_close": 0.1, "lip_rounding": 0.1, "jaw_position": 0.7},
    "E": {"mouth_open": 0.5, "mouth_close": 0.2, "lip_rounding": 0.15, "jaw_position": 0.45},
    "I": {"mouth_open": 0.3, "mouth_close": 0.3, "lip_rounding": 0.1, "jaw_position": 0.25},
    "O": {"mouth_open": 0.55, "mouth_close": 0.15, "lip_rounding": 0.75, "jaw_position": 0.5},
    "U": {"mouth_open": 0.35, "mouth_close": 0.2, "lip_rounding": 0.9, "jaw_position": 0.3},
    "P": {"mouth_open": 0.0, "mouth_close": 1.0, "lip_rounding": 0.2, "jaw_position": 0.1},
    "F": {"mouth_open": 0.15, "mouth_close": 0.6, "lip_rounding": 0.3, "jaw_position": 0.2},
    "T": {"mouth_open": 0.2, "mouth_close": 0.4, "lip_rounding": 0.1, "jaw_position": 0.25},
    "S": {"mouth_open": 0.25, "mouth_close": 0.35, "lip_rounding": 0.2, "jaw_position": 0.2},
    "K": {"mouth_open": 0.35, "mouth_close": 0.25, "lip_rounding": 0.15, "jaw_position": 0.4},
    "L": {"mouth_open": 0.3, "mouth_close": 0.3, "lip_rounding": 0.1, "jaw_position": 0.3},
    "R": {"mouth_open": 0.35, "mouth_close": 0.25, "lip_rounding": 0.4, "jaw_position": 0.35},
    "W": {"mouth_open": 0.3, "mouth_close": 0.25, "lip_rounding": 0.85, "jaw_position": 0.3},
    "Y": {"mouth_open": 0.25, "mouth_close": 0.3, "lip_rounding": 0.2, "jaw_position": 0.25},
}


@dataclass
class PhonemeToken:
    ipa: str
    duration_ms: float
    stress: float = 0.0
    reduced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VisemeToken:
    viseme: str
    mouth_open: float
    mouth_close: float
    lip_rounding: float
    jaw_position: float
    timing_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grapheme_to_phonemes(text: str) -> list[str]:
    phones: list[str] = []
    for ch in (text or "").lower():
        if ch in _G2P:
            p = _G2P[ch]
            if p:
                phones.append(p)
    return phones or ["a"]


def analyze_word_phonemes(
    text: str,
    *,
    duration_ms: int,
    speech_rate: float = 1.0,
) -> list[PhonemeToken]:
    phones = grapheme_to_phonemes(text)
    n = max(1, len(phones))
    base = max(20.0, float(duration_ms) / n / max(0.7, speech_rate))
    out: list[PhonemeToken] = []
    for i, p in enumerate(phones):
        stress = 1.0 if i == 0 and len(phones) > 1 else 0.3
        reduced = base < 35 and i > 0
        out.append(
            PhonemeToken(
                ipa=p,
                duration_ms=base * (0.7 if reduced else 1.0),
                stress=stress,
                reduced=reduced,
            )
        )
    return out


def phonemes_to_visemes(phonemes: list[PhonemeToken]) -> list[VisemeToken]:
    out: list[VisemeToken] = []
    for ph in phonemes:
        v = _PHONE_TO_VISEME.get(ph.ipa, "A")
        mouth = _VISEME_MOUTH.get(v, _VISEME_MOUTH["A"])
        out.append(
            VisemeToken(
                viseme=v,
                mouth_open=mouth["mouth_open"],
                mouth_close=mouth["mouth_close"],
                lip_rounding=mouth["lip_rounding"],
                jaw_position=mouth["jaw_position"],
                timing_ms=ph.duration_ms,
            )
        )
    return out


def enrich_word_articulation(word: Any, *, speech_rate: float = 1.0) -> Any:
    """Fill phonemes/visemes on SemanticWord (P35–P37)."""
    text = str(getattr(word, "text", "") or "")
    dur = int(getattr(word, "duration_ms", 0) or 0)
    phones = analyze_word_phonemes(text, duration_ms=dur, speech_rate=speech_rate)
    vis = phonemes_to_visemes(phones)
    word.phonemes = [p.ipa for p in phones]
    word.visemes = [v.viseme for v in vis]
    if phones:
        word.stress = max(p.stress for p in phones)
    setattr(word, "phoneme_tokens", phones)
    setattr(word, "viseme_tokens", vis)
    return word
