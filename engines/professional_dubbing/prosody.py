"""Intonation and pause analysis for Professional Dubbing TTS."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.professional_dubbing.source_cues import gap_to_break_ms
from engines.semantic_adaptation import estimate_tts_duration_ms

_BREAK_COMMA = 220
_BREAK_COLON = 380
_BREAK_SENTENCE = 480
_BREAK_CLAUSE = 320
_MIN_COMMA_BREAK_UK_RU = 200
_MIN_SENTENCE_BREAK_UK_RU = 420

_LEADING_CONTRAST = re.compile(
    r"^\s*(але|a|but|however|проте|однако|та|yet|so|and)\b",
    re.IGNORECASE,
)
_CLAUSE_CONJ = re.compile(
    r"(?<![>])\b(і|та|але|проте|а|and|but|or|because|бо|тому\s+що)\b(?=[,\s])",
    re.IGNORECASE,
)
_EMPHASIS_NAMES = re.compile(
    r"\b([A-ZА-ЯІЇЄҐ][a-zа-яіїєґ]+(?:[\s-][A-ZА-ЯІЇЄҐ][a-zа-яіїєґ]+)?)\b"
)
_EMPHASIS_NUM = re.compile(r"\b(\d+(?:[.,]\d+)?%?)\b")
_EMPHASIS_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(справжн\w+\s+робот\w+)\b", re.IGNORECASE), "key_phrase"),
    (re.compile(r"\b(кожна\s+вечер\w+)\b", re.IGNORECASE), "key_phrase"),
    (re.compile(r"\b(по\s+суті)\b", re.IGNORECASE), "key_phrase"),
    (re.compile(r"\b(велику\s+суперечк\w+)\b", re.IGNORECASE), "key_phrase"),
    (re.compile(r"\b(отримаєш\s+справжню\s+робот\w+)\b", re.IGNORECASE), "key_phrase"),
    (re.compile(r"\b(настоящ\w+\s+работ\w+)\b", re.IGNORECASE), "key_phrase"),
]


@dataclass
class ProsodyPlan:
    plain_text: str
    text_for_tts: str
    suggested_rate: str | None = None
    suggested_pitch: str | None = None
    place_delay_ms: int = 0
    lead_in_ms: int = 0
    pauses: list[dict[str, Any]] = field(default_factory=list)
    accents: list[dict[str, Any]] = field(default_factory=list)
    segment_ms: int = 0
    est_ms_before: int = 0
    est_ms_after: int = 0
    fill_percent: float = 0.0
    underfill: bool = False
    decisions: list[str] = field(default_factory=list)
    source_cues: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plain_text": self.plain_text,
            "text_for_tts": self.text_for_tts,
            "suggested_rate": self.suggested_rate,
            "suggested_pitch": self.suggested_pitch,
            "place_delay_ms": self.place_delay_ms,
            "lead_in_ms": self.lead_in_ms,
            "pauses": self.pauses,
            "accents": self.accents,
            "segment_ms": self.segment_ms,
            "est_ms_before": self.est_ms_before,
            "est_ms_after": self.est_ms_after,
            "fill_percent": self.fill_percent,
            "underfill": self.underfill,
            "decisions": self.decisions,
            "source_cues": self.source_cues,
        }


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _lang_tag(lang: str) -> str:
    base = (lang or "en").split("-")[0].lower()
    mapping = {"uk": "uk-UA", "ru": "ru-RU", "en": "en-US", "de": "de-DE", "fr": "fr-FR"}
    return mapping.get(base, f"{base}-{base.upper()}")


def _parse_rate_pct(rate: str | None) -> int:
    if not rate:
        return 0
    try:
        return int(str(rate).replace("%", "").replace("+", ""))
    except ValueError:
        return 0


def _format_rate(base: str | None, delta: int) -> str:
    pct = _parse_rate_pct(base) + delta
    pct = max(-20, min(5, pct))
    if pct == 0:
        return "+0%"
    return f"{pct:+d}%"


def _compute_rate_for_fill(est_ms: int, target_ms: int, base_rate: str | None) -> str:
    if est_ms <= 0 or target_ms <= est_ms:
        return base_rate
    ratio = target_ms / est_ms
    if ratio <= 1.05:
        return base_rate
    slow_pct = int(min(14, max(4, (ratio - 1.0) * 90)))
    return _format_rate(base_rate, -slow_pct)


def _auto_place_delay(
    est_ms: int,
    segment_ms: int,
    cues: dict[str, Any],
) -> int:
    src_delay = int(cues.get("place_delay_ms") or 0)
    if src_delay > 0:
        return min(280, src_delay)
    if segment_ms <= 0 or est_ms <= 0:
        return 0
    fill = est_ms / segment_ms
    if fill < 0.68:
        return min(220, max(0, (segment_ms - est_ms) // 4))
    return 0


def _lang_min_break(ch: str, lang: str) -> int:
    base = (lang or "ru").split("-")[0].lower()
    if base not in ("uk", "ru"):
        return 0
    if ch == ",":
        return _MIN_COMMA_BREAK_UK_RU
    if ch in ".!?…":
        return _MIN_SENTENCE_BREAK_UK_RU
    if ch == ":":
        return 340
    return 0


def _scale_break_ms(ms: int, *, fill_percent: float, lang: str = "", ch: str = "") -> int:
    """When text is long for the slot, shorten pauses before speeding speech."""
    from engines.professional_dubbing.config import MAX_BREAK_MS

    floor = _lang_min_break(ch, lang)
    if fill_percent <= 108:
        out = max(ms, floor) if floor else ms
    else:
        ratio = min(1.0, 108.0 / max(fill_percent, 108.0))
        scaled = int(ms * max(0.78, ratio))
        if floor:
            scaled = max(scaled, int(floor * 0.92))
        out = max(140, scaled)
    # TZ v4.0 P2: never exceed MAX_BREAK_MS (350)
    return min(int(out), int(MAX_BREAK_MS))


_ABBREV_BEFORE_DOT = re.compile(
    r"(?:^|[\s\(])(?:Jr|Sr|Mr|Mrs|Ms|Dr|Prof|St|vs|etc|т\s*\.?\s*д|т\s*\.?\s*п)$",
    re.I,
)
_NAME_BEFORE_DOT = re.compile(
    r"(?:Джордж-молодший|Джорджа-молодшого|George\s+Jr)$",
    re.I,
)


def _is_abbrev_or_midname_dot(plain: str, dot_index: int) -> bool:
    """True when '.' is abbreviation / mid-name, not a sentence end."""
    before = plain[:dot_index].rstrip()
    if _ABBREV_BEFORE_DOT.search(before) or _NAME_BEFORE_DOT.search(before):
        after = plain[dot_index + 1 :].lstrip()
        # Sentence end if nothing after
        if not after:
            return False
        # Continuation lowercase / Ukrainian lowercase → mid-name / false period
        if after[0].islower() or after[0] in "аеиоуяюєіїьбвгґджзйклмнпрстфхцчшщ":
            return True
        # Abbreviation always skip break even before capital (Jr. Drove)
        if _ABBREV_BEFORE_DOT.search(before):
            return True
    return False


def build_prosody_plan(
    text: str,
    *,
    segment_ms: int,
    lang: str = "ru",
    base_rate: str | None = None,
    base_pitch: str | None = None,
    use_ssml: bool = True,
    source_cues: dict[str, Any] | None = None,
    is_continuation: bool = False,
) -> ProsodyPlan:
    plain = " ".join(str(text or "").split())
    cues = dict(source_cues or {})
    plan = ProsodyPlan(
        plain_text=plain,
        text_for_tts=plain,
        segment_ms=segment_ms,
        source_cues=cues,
    )
    if not plain:
        return plan

    est = estimate_tts_duration_ms(plain, lang)
    plan.est_ms_before = est
    plan.fill_percent = round(100.0 * est / max(segment_ms, 1), 1)
    target_ms = int(segment_ms * 0.96)
    plan.underfill = est < int(segment_ms * 0.93)

    rate_delta = int(cues.get("suggested_rate_slow") or 0)
    # Softer studio delivery: slightly slower + lower pitch.
    plan.suggested_rate = _format_rate(base_rate, rate_delta - 4)
    plan.suggested_pitch = base_pitch or "-2Hz"

    extra_break_budget = 0
    if plan.underfill:
        gap = max(0, target_ms - est)
        extra_break_budget = min(720, int(gap * 0.5))
        plan.suggested_rate = _compute_rate_for_fill(est, target_ms, plan.suggested_rate)
        plan.decisions.append(f"underfill {plan.fill_percent}% → rate {plan.suggested_rate}")
    else:
        plan.decisions.append(f"natural rate {plan.suggested_rate}")
        if plan.fill_percent > 112:
            boost = int(min(6, max(2, (plan.fill_percent - 108) * 0.35)))
            plan.suggested_rate = _format_rate(plan.suggested_rate, boost)
            plan.decisions.append(
                f"overfill {plan.fill_percent}% → light faster {plan.suggested_rate} (pauses scaled)"
            )

    plan.place_delay_ms = _auto_place_delay(est, segment_ms, cues)
    if plan.place_delay_ms:
        plan.decisions.append(f"place_delay {plan.place_delay_ms}ms from source rhythm")

    lang_base = (lang or "ru").split("-")[0].lower()

    if not use_ssml:
        plan.est_ms_after = est + extra_break_budget
        plan.fill_percent = round(100.0 * plan.est_ms_after / max(segment_ms, 1), 1)
        return plan

    parts: list[str] = []
    lead_break = int(cues.get("lead_break_ms") or 0)
    if _LEADING_CONTRAST.match(plain):
        lead_break = max(lead_break, 360 if is_continuation else 280)
        plan.decisions.append("leading contrast pause before clause")
    # Timeline place_delay already shifts dub start — avoid double silence at lip entry.
    if plan.place_delay_ms >= 80:
        if lead_break > 0:
            plan.decisions.append("lead_break skipped (place_delay on timeline)")
        lead_break = 0
    if lead_break > 0:
        parts.append(f'<break time="{lead_break}ms"/>')
        plan.lead_in_ms = lead_break
        plan.pauses.append({"before": "start", "ms": lead_break, "type": "source_lead"})

    source_gaps = list(cues.get("internal_gaps_ms") or [])
    gap_idx = 0

    i = 0
    n = len(plain)
    while i < n:
        ch = plain[i]
        if ch in ",;:":
            ms = _BREAK_COMMA if ch == "," else _BREAK_COLON
            ms = max(ms, _lang_min_break(ch, lang_base))
            if source_gaps and gap_idx < len(source_gaps):
                mapped = gap_to_break_ms(int(source_gaps[gap_idx]))
                gap_idx += 1
                if mapped is not None:
                    ms = max(ms, mapped)
            ms = _scale_break_ms(ms, fill_percent=plan.fill_percent, lang=lang_base, ch=ch)
            parts.append(ch)
            parts.append(f'<break time="{ms}ms"/>')
            plan.pauses.append({"after": ch, "ms": ms, "type": "punctuation"})
            i += 1
            continue
        if ch in ".!?…":
            # TZ v4.0 P2: no sentence break after Jr. / mid-name false period
            if ch == "." and _is_abbrev_or_midname_dot(plain, i):
                parts.append(ch)
                i += 1
                continue
            ms = _BREAK_SENTENCE
            ms = max(ms, _lang_min_break(ch, lang_base))
            if extra_break_budget > 0:
                add = min(140, extra_break_budget)
                ms += add
                extra_break_budget -= add
            ms = _scale_break_ms(ms, fill_percent=plan.fill_percent, lang=lang_base, ch=ch)
            parts.append(ch)
            parts.append(f'<break time="{ms}ms"/>')
            plan.pauses.append({"after": "sentence_end", "ms": ms, "type": "thought_boundary"})
            i += 1
            continue
        parts.append(ch)
        i += 1

    body = "".join(parts)

    def _conj_repl(m: re.Match) -> str:
        word = m.group(0)
        low = word.lower()
        start = m.start()
        before = body[max(0, start - 48) : start]
        if "break time" in before and low in (
            "але",
            "but",
            "проте",
            "however",
            "a",
            "and",
            "і",
            "та",
        ):
            return word
        if lead_break > 0 and start < 48 and low in (
            "але",
            "but",
            "проте",
            "however",
            "a",
            "and",
        ):
            return word
        if low in ("але", "but", "проте", "however", "a", "and"):
            br = _scale_break_ms(_BREAK_CLAUSE, fill_percent=plan.fill_percent, lang=lang_base, ch=",")
            return f'<break time="{br}ms"/>{word}'
        if low in ("і", "та"):
            return word
        br = _scale_break_ms(min(200, _BREAK_CLAUSE - 80), fill_percent=plan.fill_percent, lang=lang_base, ch=",")
        return f'{word}<break time="{br}ms"/>'

    body = _CLAUSE_CONJ.sub(_conj_repl, body, count=4)
    body = re.sub(
        r'(<break time="\d+ms"/>)\s*(<break time="\d+ms"/>)',
        lambda m: m.group(1),
        body,
    )

    accent_count = 0
    for m in _EMPHASIS_NUM.finditer(plain):
        if accent_count >= 2:
            break
        word = m.group(0)
        body = body.replace(word, f'<emphasis level="reduced">{_xml_escape(word)}</emphasis>', 1)
        plan.accents.append({"word": word, "type": "number"})
        accent_count += 1

    for m in _EMPHASIS_NAMES.finditer(plain):
        if accent_count >= 3:
            break
        word = m.group(0)
        if len(word) < 3 or word.lower() in ("але", "and", "the", "і", "та"):
            continue
        safe = re.escape(word)
        body, n = re.subn(
            rf"(?<![>])\b{safe}\b",
            f'<emphasis level="reduced">{_xml_escape(word)}</emphasis>',
            body,
            count=1,
        )
        if n:
            plan.accents.append({"word": word, "type": "name"})
            accent_count += 1

    lang_base = (lang or "ru").split("-")[0].lower()
    if lang_base in ("uk", "ru"):
        for pat, kind in _EMPHASIS_PHRASES:
            if accent_count >= 5:
                break
            m = pat.search(plain)
            if not m:
                continue
            phrase = m.group(1)
            safe = re.escape(phrase)
            body, n = re.subn(
                rf"(?<![>]){safe}",
                f'<emphasis level="moderate">{_xml_escape(phrase)}</emphasis>',
                body,
                count=1,
            )
            if n:
                plan.accents.append({"word": phrase, "type": kind})
                accent_count += 1

    xml_lang = _lang_tag(lang)
    plan.text_for_tts = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{xml_lang}">{body}</speak>'
    )
    plan.est_ms_after = est + sum(p.get("ms", 0) for p in plan.pauses)
    if plan.suggested_rate:
        pct = _parse_rate_pct(plan.suggested_rate)
        if pct < 0:
            plan.est_ms_after = int(plan.est_ms_after * (1.0 - pct / 100.0))
    plan.fill_percent = round(100.0 * plan.est_ms_after / max(segment_ms, 1), 1)
    return plan
