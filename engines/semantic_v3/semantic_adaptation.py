"""P106 — Semantic Adaptation: multi-variant natural formulation engine.

Goal: NOT to shorten text, but to find the natural formulation that:
- preserves meaning
- fits the time slot
- sounds natural in target language

For each MeaningUnit, generates minimum 5 variants (A-E).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.semantic_v3.semantic_adaptation")


@dataclass
class AdaptationVariant:
    """One translation/adaptation variant for a MeaningUnit."""

    variant_id: str = ""
    label: str = ""  # A, B, C, D, E
    text: str = ""
    strategy: str = ""  # direct|reorder|compact|expand|cultural

    # ЭТАП 8 scores (P108 originals + Character Consistency + Localization Quality)
    meaning_score: float = 100.0
    naturalness_score: float = 100.0
    dialogue_score: float = 100.0
    duration_score: float = 100.0
    emotion_score: float = 100.0
    prosody_score: float = 100.0
    lipsync_readiness: float = 100.0
    character_consistency_score: float = 100.0
    localization_quality_score: float = 100.0
    runtime_cost: float = 0.0

    # P107 duration prediction
    predicted_duration_ms: int = 0
    prediction_confidence: float = 0.0

    # selection
    selected: bool = False
    rejected: bool = False
    reject_reasons: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.variant_id:
            self.variant_id = uuid.uuid4().hex[:12]

    def composite_score(self) -> float:
        """Weighted composite score across the full ЭТАП 8 dimension set."""
        return (
            self.meaning_score * 0.24
            + self.duration_score * 0.18
            + self.naturalness_score * 0.14
            + self.emotion_score * 0.08
            + self.dialogue_score * 0.08
            + self.character_consistency_score * 0.08
            + self.lipsync_readiness * 0.06
            + self.prosody_score * 0.06
            + self.localization_quality_score * 0.08
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "label": self.label,
            "text": self.text,
            "strategy": self.strategy,
            "meaning_score": self.meaning_score,
            "naturalness_score": self.naturalness_score,
            "dialogue_score": self.dialogue_score,
            "duration_score": self.duration_score,
            "emotion_score": self.emotion_score,
            "prosody_score": self.prosody_score,
            "lipsync_readiness": self.lipsync_readiness,
            "character_consistency_score": self.character_consistency_score,
            "localization_quality_score": self.localization_quality_score,
            "runtime_cost": self.runtime_cost,
            "predicted_duration_ms": self.predicted_duration_ms,
            "prediction_confidence": self.prediction_confidence,
            "selected": self.selected,
            "rejected": self.rejected,
            "reject_reasons": self.reject_reasons,
            "composite_score": self.composite_score(),
        }


VARIANT_LABELS = ["A", "B", "C", "D", "E"]

# P117 — elements that MUST be preserved
_NUMBERS = re.compile(r'\b\d+(?:[.,]\d+)?\b')
_NEGATIONS_EN = re.compile(r"\b(not|no|never|none|neither|nor|n't|cannot|don't|doesn't|didn't|won't|wouldn't|couldn't|shouldn't)\b", re.I)
_NEGATIONS_UK = re.compile(r"\b(не|ні|ніколи|жодний|жодна|жодне|ніде|нікуди|ніхто|ніщо)\b", re.I)


def generate_adaptation_variants(
    unit: Any,
    *,
    translated_text: str = "",
    source_text: str = "",
    slot_ms: int = 0,
    tgt_lang: str = "uk",
    style: str = "",
    emotion: str = "neutral",
) -> list[AdaptationVariant]:
    """P106: generate minimum 5 natural formulation variants for a MeaningUnit.

    Strategies:
    A — Direct (faithful translation, minimal changes)
    B — Reordered (natural word order for target language)
    C — Compact (shorter equivalent expressions, no meaning loss)
    D — Expanded (more natural/explicit phrasing when time allows)
    E — Cultural (localized expressions that preserve meaning)
    """
    text = translated_text or getattr(unit, 'translated_text', '') or getattr(unit, 'text', '') or ''
    src = source_text or getattr(unit, 'text', '') or ''
    slot = slot_ms or getattr(unit, 'slot_ms', 0) or 0

    if not text.strip():
        return []

    variants: list[AdaptationVariant] = []

    # Strategy A: Direct translation (as-is)
    var_a = AdaptationVariant(
        label="A",
        text=text,
        strategy="direct",
        meaning_score=100.0,
        naturalness_score=85.0,
    )
    variants.append(var_a)

    # Strategy B: Reordered for target language naturalness
    reordered = _reorder_for_naturalness(text, tgt_lang)
    if reordered != text:
        var_b = AdaptationVariant(
            label="B",
            text=reordered,
            strategy="reorder",
            meaning_score=98.0,
            naturalness_score=95.0,
        )
    else:
        var_b = AdaptationVariant(
            label="B",
            text=text,
            strategy="reorder",
            meaning_score=100.0,
            naturalness_score=85.0,
        )
    variants.append(var_b)

    # Strategy C: Compact (shorter equivalent expressions)
    compact = _compact_expression(text, tgt_lang)
    if compact != text:
        var_c = AdaptationVariant(
            label="C",
            text=compact,
            strategy="compact",
            meaning_score=95.0,
            naturalness_score=90.0,
        )
    else:
        var_c = AdaptationVariant(
            label="C",
            text=text,
            strategy="compact",
            meaning_score=100.0,
            naturalness_score=85.0,
        )
    variants.append(var_c)

    # Strategy D: semantically equivalent expansion when Target needs it.
    from engines.semantic_v3.variant_duration_predictor import predict_variant_duration

    current_ms, _ = predict_variant_duration(text, lang=tgt_lang)
    expanded = _expand_expression(
        text,
        tgt_lang,
        target_ms=slot,
        current_ms=current_ms,
    )
    if expanded != text:
        var_d = AdaptationVariant(
            label="D",
            text=expanded,
            strategy="expand",
            meaning_score=100.0,
            naturalness_score=92.0,
        )
    else:
        var_d = AdaptationVariant(
            label="D",
            text=text,
            strategy="expand",
            meaning_score=100.0,
            naturalness_score=85.0,
        )
    variants.append(var_d)

    # Strategy E: Cultural localization
    cultural = _cultural_localize(text, tgt_lang)
    if cultural != text:
        var_e = AdaptationVariant(
            label="E",
            text=cultural,
            strategy="cultural",
            meaning_score=95.0,
            naturalness_score=97.0,
        )
    else:
        var_e = AdaptationVariant(
            label="E",
            text=text,
            strategy="cultural",
            meaning_score=100.0,
            naturalness_score=85.0,
        )
    variants.append(var_e)

    # P117 validation on all variants
    for var in variants:
        violations = _check_meaning_preservation(src, var.text, text)
        if violations:
            var.rejected = True
            var.reject_reasons.extend(violations)
            var.meaning_score = max(0, var.meaning_score - 30 * len(violations))

    logger.info(
        "SemanticAdaptation: unit=%s variants=%d rejected=%d",
        getattr(unit, 'unit_uuid', '?')[:8],
        len(variants),
        sum(1 for v in variants if v.rejected),
    )
    return variants


def select_best_variant(
    variants: list[AdaptationVariant],
    *,
    slot_ms: int = 0,
    prefer_strategy: str = "",
) -> AdaptationVariant | None:
    """P108: select best variant by composite score. Returns None if all rejected."""
    if not variants:
        return None

    alive = [v for v in variants if not v.rejected]
    pool = alive or variants

    best = max(pool, key=lambda v: v.composite_score())
    best.selected = True
    return best


def _reorder_for_naturalness(text: str, lang: str) -> str:
    """Attempt word-order adjustments typical for the target language."""
    # Slavic languages prefer SVO but allow flexibility
    # Move time expressions to sentence start (common in UK/RU)
    time_patterns = re.compile(
        r',?\s*(yesterday|today|tomorrow|then|now|later|'
        r'вчора|сьогодні|завтра|тоді|зараз|потім)\s*,?',
        re.I,
    )
    match = time_patterns.search(text)
    if match and match.start() > len(text) // 3:
        word = match.group(1).strip()
        cleaned = time_patterns.sub('', text).strip()
        cleaned = cleaned.lstrip(',').strip()
        if cleaned:
            return f"{word.capitalize()}, {cleaned[0].lower()}{cleaned[1:]}" if len(cleaned) > 1 else f"{word.capitalize()}, {cleaned}"
    return text


def _compact_expression(text: str, lang: str) -> str:
    """Replace verbose constructions with shorter equivalents. No meaning loss allowed."""
    replacements_uk = [
        (r'\bу зв\'язку з тим, що\b', 'бо'),
        (r'\bдля того, щоб\b', 'щоб'),
        (r'\bу той час як\b', 'коли'),
        (r'\bнезважаючи на те, що\b', 'хоча'),
        (r'\bвнаслідок того, що\b', 'бо'),
        (r'\bз причини того, що\b', 'бо'),
        (r'\bв даний момент\b', 'зараз'),
        (r'\bна даний момент\b', 'зараз'),
    ]
    replacements_en = [
        (r'\bin order to\b', 'to'),
        (r'\bdue to the fact that\b', 'because'),
        (r'\bat this point in time\b', 'now'),
        (r'\bin the event that\b', 'if'),
        (r'\bfor the purpose of\b', 'to'),
        (r'\bwith regard to\b', 'about'),
    ]

    result = text
    repls = replacements_uk if lang in ('uk', 'ua', 'ru') else replacements_en
    for pattern, repl in repls:
        result = re.sub(pattern, repl, result, flags=re.I)
    return result


def _expand_expression(
    text: str,
    lang: str,
    *,
    target_ms: int = 0,
    current_ms: int = 0,
) -> str:
    """Use equivalent natural forms; never append generic timing filler."""
    if target_ms <= 0 or current_ms >= target_ms * 0.88:
        return text

    stripped = text.strip()
    terminal = stripped[-1:] if stripped[-1:] in ".!?" else "."
    core = stripped.rstrip(".!?").strip().lower()

    if lang in ("uk", "ua"):
        exact = {
            "так": f"Так, авжеж{terminal}",
            "ні": f"Ні, це не так{terminal}",
            "гаразд": f"Гаразд, домовилися{terminal}",
            "добре": f"Добре, нехай буде так{terminal}",
        }
        if core in exact:
            return exact[core]
        replacements = [
            (r"\bбо\b", "тому що"),
            (r"\bщоб\b", "для того, щоб"),
            (r"\bзараз\b", "прямо зараз"),
        ]
    elif lang == "ru":
        exact = {
            "да": f"Да, конечно{terminal}",
            "нет": f"Нет, это не так{terminal}",
            "хорошо": f"Хорошо, договорились{terminal}",
            "ладно": f"Ладно, пусть будет так{terminal}",
        }
        if core in exact:
            return exact[core]
        replacements = [
            (r"\bпотому что\b", "по той причине, что"),
            (r"\bчтобы\b", "для того, чтобы"),
            (r"\bсейчас\b", "прямо сейчас"),
        ]
    else:
        exact = {
            "yes": f"Yes, indeed{terminal}",
            "no": f"No, that's not right{terminal}",
            "okay": f"Okay, that works for me{terminal}",
            "ok": f"Okay, that works for me{terminal}",
            "sure": f"Yes, of course{terminal}",
        }
        if core in exact:
            return exact[core]
        replacements = [
            (r"\bI'll\b", "I will"),
            (r"\bcan't\b", "cannot"),
            (r"\bdon't\b", "do not"),
            (r"\bdidn't\b", "did not"),
            (r"\bit's\b", "it is"),
        ]

    result = text
    for pattern, replacement in replacements:
        candidate = re.sub(pattern, replacement, result, count=1, flags=re.I)
        if candidate != result:
            return candidate
    return text


def _cultural_localize(text: str, lang: str) -> str:
    """Replace culture-specific references with target-culture equivalents."""
    return text


def _check_meaning_preservation(
    source: str, variant: str, reference: str
) -> list[str]:
    """P117: verify that the variant preserves critical meaning elements."""
    violations = []

    # Check numbers preserved
    src_nums = set(_NUMBERS.findall(source))
    ref_nums = set(_NUMBERS.findall(reference))
    var_nums = set(_NUMBERS.findall(variant))
    critical_nums = src_nums | ref_nums
    missing_nums = critical_nums - var_nums
    if missing_nums:
        violations.append(f"missing_numbers: {missing_nums}")

    # Check negations preserved
    src_negs = bool(_NEGATIONS_EN.search(source))
    ref_negs = bool(_NEGATIONS_UK.search(reference))
    var_negs = bool(_NEGATIONS_UK.search(variant)) or bool(_NEGATIONS_EN.search(variant))
    if (src_negs or ref_negs) and not var_negs:
        violations.append("negation_lost")

    return violations
