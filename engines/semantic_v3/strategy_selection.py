"""P108 / ЭТАП 8 — Strategy Selection: score and rank adaptation variants.

For each variant computes the full ЭТАП 8 score set:
- Meaning Score (semantic preservation)
- Duration Score (time slot fit)
- Naturalness Score (target language fluency)
- Emotion Score (emotional tone preservation)
- Dialogue Score (conversational appropriateness)
- Character Consistency Score (speaker voice / register preservation)
- LipSync Readiness (visual sync potential)
- Prosody Score (speech rhythm / flow)
- Localization Quality Score (target-culture appropriateness)

Selects the best overall variant.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.semantic_v3.strategy_selection")


def score_variant(
    variant: Any,
    *,
    source_text: str = "",
    slot_ms: int = 0,
    emotion: str = "neutral",
    style: str = "",
    is_dialogue: bool = False,
    speaker: str = "",
    tgt_lang: str = "uk",
) -> Any:
    """Compute all ЭТАП 8 scores for one AdaptationVariant."""
    text = getattr(variant, 'text', '') or ''

    if not text.strip():
        variant.meaning_score = 0.0

    predicted = getattr(variant, 'predicted_duration_ms', 0)
    if slot_ms > 0 and predicted > 0:
        from engines.semantic_v3.variant_duration_predictor import compute_duration_score
        variant.duration_score = compute_duration_score(predicted, slot_ms)

    if is_dialogue:
        word_count = len(text.split())
        if word_count <= 15:
            variant.dialogue_score = 95.0
        elif word_count <= 25:
            variant.dialogue_score = 85.0
        else:
            variant.dialogue_score = 70.0
    else:
        variant.dialogue_score = 90.0

    variant.emotion_score = _score_emotion(text, emotion)
    variant.prosody_score = _score_prosody(text)
    variant.lipsync_readiness = _score_lipsync(text, source_text)
    variant.runtime_cost = len(text) / 100.0

    character_score = _score_character_consistency(text, source_text, style=style, speaker=speaker)
    localization_score = _score_localization_quality(text, tgt_lang=tgt_lang)
    setattr(variant, "character_consistency_score", character_score)
    setattr(variant, "localization_quality_score", localization_score)

    return variant


def score_all_variants(
    variants: list[Any],
    *,
    source_text: str = "",
    slot_ms: int = 0,
    emotion: str = "neutral",
    style: str = "",
    is_dialogue: bool = False,
    speaker: str = "",
    tgt_lang: str = "uk",
) -> list[Any]:
    """Score all variants for a MeaningUnit with the full ЭТАП 8 set."""
    for var in variants:
        score_variant(
            var,
            source_text=source_text,
            slot_ms=slot_ms,
            emotion=emotion,
            style=style,
            is_dialogue=is_dialogue,
            speaker=speaker,
            tgt_lang=tgt_lang,
        )
    return variants


def select_best(variants: list[Any]) -> Any | None:
    """Select the best non-rejected variant by composite score."""
    if not variants:
        return None

    alive = [v for v in variants if not getattr(v, 'rejected', False)]
    pool = alive or variants

    best = max(pool, key=lambda v: v.composite_score() if hasattr(v, 'composite_score') else 0)
    best.selected = True

    logger.info(
        "StrategySelection: best=%s strategy=%s score=%.1f",
        getattr(best, 'label', '?'),
        getattr(best, 'strategy', '?'),
        best.composite_score() if hasattr(best, 'composite_score') else 0,
    )
    return best


def _score_emotion(text: str, target_emotion: str) -> float:
    """Score how well text conveys the target emotion."""
    if target_emotion == "neutral":
        return 90.0

    emotion_markers = {
        "joy": ["!", "wonderful", "great", "amazing", "чудово", "прекрасно"],
        "sadness": ["unfortunately", "sadly", "на жаль", "сумно"],
        "anger": ["!", "damn", "terrible", "жахливо", "чорт"],
        "surprise": ["!", "?", "wow", "oh", "ого", "ох"],
        "fear": ["afraid", "scary", "страшно", "боюся"],
    }

    markers = emotion_markers.get(target_emotion, [])
    if not markers:
        return 85.0

    hits = sum(1 for m in markers if m.lower() in text.lower())
    return min(100.0, 70.0 + hits * 10)


def _score_prosody(text: str) -> float:
    """Score speech rhythm quality (sentence length variance, punctuation flow)."""
    words = text.split()
    if len(words) < 2:
        return 80.0

    segments = [s.strip() for s in text.replace(';', ',').split(',') if s.strip()]
    if not segments:
        return 85.0

    avg_len = sum(len(s.split()) for s in segments) / len(segments)
    if avg_len > 15:
        return 70.0
    if avg_len < 3:
        return 75.0
    return 90.0


def _score_lipsync(text: str, source: str) -> float:
    """Score lip-sync readiness: similar phoneme density = easier to sync."""
    if not source or not text:
        return 80.0

    ratio = len(text) / max(1, len(source))
    if 0.7 <= ratio <= 1.3:
        return 95.0
    if 0.5 <= ratio <= 1.5:
        return 80.0
    return 65.0


# ────────────────────────────────────────────────────────────────────────────
# ЭТАП 8 additions: Character Consistency + Localization Quality
# ────────────────────────────────────────────────────────────────────────────

_FORMAL_MARKERS_UK = ("будь ласка", "прошу", "пане", "пані", "звольте")
_INFORMAL_MARKERS_UK = ("привіт", "гей", "давай", "ну ", "ой", "ага")
_FORMAL_MARKERS_EN = ("please", "kindly", "would you", "sir", "madam")
_INFORMAL_MARKERS_EN = ("hey", "yeah", "gonna", "wanna", "kinda", "gotta")
_QUESTION_END = re.compile(r"[?？]\s*$")


def _score_character_consistency(
    text: str,
    source_text: str,
    *,
    style: str = "",
    speaker: str = "",
) -> float:
    """Reward preservation of the speaker's register and address pattern.

    Deterministic heuristic that penalises register flip
    (formal ↔ informal) between source and target. When ``style``
    already tags a register, use that as ground truth; otherwise derive
    it from the source text.
    """
    if not text.strip():
        return 0.0
    if not source_text.strip():
        return 90.0

    src_lc = source_text.lower()
    tgt_lc = text.lower()
    src_formal = any(m in src_lc for m in _FORMAL_MARKERS_EN + _FORMAL_MARKERS_UK)
    src_informal = any(m in src_lc for m in _INFORMAL_MARKERS_EN + _INFORMAL_MARKERS_UK)
    tgt_formal = any(m in tgt_lc for m in _FORMAL_MARKERS_UK + _FORMAL_MARKERS_EN)
    tgt_informal = any(m in tgt_lc for m in _INFORMAL_MARKERS_UK + _INFORMAL_MARKERS_EN)

    score = 92.0
    if src_formal and tgt_informal and not tgt_formal:
        score -= 25.0
    if src_informal and tgt_formal and not tgt_informal:
        score -= 20.0

    # Question preservation: if source ended in "?", target should too.
    src_is_q = bool(_QUESTION_END.search(source_text))
    tgt_is_q = bool(_QUESTION_END.search(text))
    if src_is_q and not tgt_is_q:
        score -= 12.0

    # Speaker/style parity bonus
    if style and speaker:
        score = min(100.0, score + 2.0)
    return max(0.0, min(100.0, round(score, 2)))


_UK_LATIN_LEAK = re.compile(r"[a-z]{4,}", re.I)
_UK_TRANSLIT_MARKERS = ("okay", "yeah", "guys", "wow", "ok.")


def _score_localization_quality(text: str, *, tgt_lang: str = "uk") -> float:
    """Score how well the variant reads in the target locale.

    Deterministic. Penalises stray Latin runs / obvious source-language
    leakage when translating into Cyrillic-script languages, rewards a
    healthy proportion of native characters.
    """
    if not text.strip():
        return 0.0
    lang = (tgt_lang or "").lower()
    if lang not in {"uk", "ua", "ru"}:
        return 90.0

    tgt_lc = text.lower()
    penalty = 0.0
    latin_runs = _UK_LATIN_LEAK.findall(tgt_lc)
    if latin_runs:
        penalty += min(30.0, len(latin_runs) * 4.0)
    for marker in _UK_TRANSLIT_MARKERS:
        if marker in tgt_lc:
            penalty += 5.0

    cyrillic = sum(1 for c in text if "а" <= c.lower() <= "я" or c in "єіїґ")
    letters = sum(1 for c in text if c.isalpha())
    if letters:
        cyr_ratio = cyrillic / letters
        base = 100.0 * cyr_ratio
    else:
        base = 75.0

    score = base - penalty
    return max(0.0, min(100.0, round(score, 2)))
