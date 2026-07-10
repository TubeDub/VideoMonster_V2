"""Segment difficulty assessment and model routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from engines.llm_orchestrator.model_pool import LLMModelInfo, LLMModelPool, ModelTier

# Proper nouns, abbreviations, digits — signals for "complex" routing.
_ABBREV_RE = re.compile(r"\b[A-Z]{2,}\b")
_DIGIT_RE = re.compile(r"\d")
_QUOTE_RE = re.compile(r'["\'«»]')
_LATIN_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")


@dataclass
class SegmentDifficulty:
    score: float  # 0.0 = trivial, 1.0 = very complex
    tier: ModelTier
    reasons: list[str]

    @property
    def needs_strong_model(self) -> bool:
        return self.tier == ModelTier.STRONG

    @property
    def can_use_light_model(self) -> bool:
        return self.tier == ModelTier.LIGHT


def assess_segment_difficulty(
    source_text: str,
    translated_text: str = "",
    *,
    target_lang: str = "",
    context_before: str = "",
    context_after: str = "",
) -> SegmentDifficulty:
    """Score segment complexity for model tier selection.

  Quality rule: when in doubt, route to STANDARD or STRONG — never downgrade
  a segment that carries names, abbreviations or long context.
    """
    reasons: list[str] = []
    score = 0.0
    src = str(source_text or "")
    tr = str(translated_text or "")
    combined = f"{src} {tr}".strip()
    word_count = len(combined.split())

    if word_count <= 8:
        score += 0.0
    elif word_count <= 20:
        score += 0.15
    elif word_count <= 40:
        score += 0.35
        reasons.append("long_segment")
    else:
        score += 0.55
        reasons.append("very_long_segment")

    # Named entities / abbreviations
    abbrevs = _ABBREV_RE.findall(src)
    if abbrevs:
        score += min(0.35, 0.12 * len(abbrevs))
        reasons.append(f"abbreviations:{','.join(abbrevs[:4])}")

    names = _LATIN_NAME_RE.findall(src)
    if len(names) >= 2:
        score += 0.2
        reasons.append("multiple_proper_names")

    if _DIGIT_RE.search(src):
        score += 0.08
        reasons.append("numbers")

    if _QUOTE_RE.search(src):
        score += 0.1
        reasons.append("quoted_speech")

    # Cross-sentence context dependency
    if context_before.strip() or context_after.strip():
        score += 0.12
        reasons.append("dialogue_context")

    # Idiomatic / figurative cues (English)
    idioms = (
        "near-death", "groundbreaking", "franchise", "obsession",
        "dreading", "prestigious", "cinematograph",
    )
    low_src = src.lower()
    if any(idm in low_src for idm in idioms):
        score += 0.2
        reasons.append("idiomatic_language")

    # Translation already looks broken → always strong
    if tr:
        if _ABBREV_RE.findall(src) and not any(
            a.lower() in tr.lower() for a in abbrevs
        ):
            score += 0.25
            reasons.append("entity_risk_in_translation")

    score = min(1.0, max(0.0, score))

    if score >= 0.55:
        tier = ModelTier.STRONG
    elif score >= 0.25:
        tier = ModelTier.STANDARD
    else:
        tier = ModelTier.LIGHT

    return SegmentDifficulty(score=round(score, 3), tier=tier, reasons=reasons)


def route_segment(
    pool: LLMModelPool,
    difficulty: SegmentDifficulty,
    *,
    allow_light: bool = True,
    require_adequate: bool = True,
) -> LLMModelInfo | None:
    """Pick the best available model for this segment's difficulty.

    ``require_adequate``: if True, never pick a model below 7B for dubbing
    unless no adequate model exists (then pick strongest available).
    """
    tier = difficulty.tier
    if not allow_light and tier == ModelTier.LIGHT:
        tier = ModelTier.STANDARD

    model = pool.best_for_tier(tier, prefer_idle=True)
    if model and require_adequate and not model.adequate:
        # Prefer adequate standard/strong
        for t in (ModelTier.STRONG, ModelTier.STANDARD, ModelTier.LIGHT):
            alt = pool.best_for_tier(t, prefer_idle=True)
            if alt and alt.adequate:
                return alt
    return model


def backup_model(
    pool: LLMModelPool,
    primary: LLMModelInfo,
) -> LLMModelInfo | None:
    """Reserve model for timeout/low-confidence retry — not used on every segment."""
    # Prefer a different tier or larger model
    if primary.tier != ModelTier.STRONG:
        strong = pool.best_for_tier(ModelTier.STRONG, prefer_idle=True)
        if strong and strong.name != primary.name:
            return strong
    standard = pool.best_for_tier(ModelTier.STANDARD, prefer_idle=True)
    if standard and standard.name != primary.name:
        return standard
    # Any other idle model
    for m in pool.idle_models():
        if m.name != primary.name and m.adequate:
            return m
    return None
