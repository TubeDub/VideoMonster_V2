"""P10 Adaptation order + P11 Semantic Rewrite + P12 Sentence Merge + P13 Dynamic segments."""

from __future__ import annotations

import re
from typing import Callable

from engines.semantic_v3.semantic_lock import (
    assert_semantic_rewrite_allowed,
    entity_preservation_score,
)
from engines.semantic_v3.types import SemanticSentence

# P11 — deterministic contractions / short forms (EN→natural; UK light)
_REWRITE_PAIRS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bI am going to\b", re.I), "I'll"),
    (re.compile(r"\bI will\b", re.I), "I'll"),
    (re.compile(r"\bdo not\b", re.I), "don't"),
    (re.compile(r"\bdid not\b", re.I), "didn't"),
    (re.compile(r"\bcannot\b", re.I), "can't"),
    (re.compile(r"\bwould not\b", re.I), "wouldn't"),
    (re.compile(r"\bв зв'язку з тим, що\b", re.I), "тому що"),
    (re.compile(r"\bу той момент, коли\b", re.I), "коли"),
    (re.compile(r"\bпісля цього\b", re.I), "потім"),
    (re.compile(r"\bнадзвичайно\b", re.I), "дуже"),
]


ADAPTATION_ORDER = (
    "remove_extra_pauses",
    "trim_silence",
    "ssml_prosody",
    "tempo",
    "micro_stretch",
    "borrow_time",
    "gap_redistribution",
    "sentence_merge",
    "multi_sentence_merge",
    "semantic_rewrite",
)


def semantic_rewrite(text: str) -> str:
    """P11 — construction change without mechanical chopping."""
    out = " ".join(str(text or "").split())
    for pat, repl in _REWRITE_PAIRS:
        out = pat.sub(repl, out)
    return " ".join(out.split())


def try_semantic_rewrite_sentence(sent: SemanticSentence) -> SemanticSentence:
    src = sent.translated_text or sent.text
    rewritten = semantic_rewrite(src)
    if rewritten == src:
        return sent
    score = entity_preservation_score(sent, rewritten)
    # Deterministic meaning proxy: length ratio near 1 and entities intact
    meaning = min(1.0, len(rewritten) / max(1, len(src))) if len(rewritten) <= len(src) else 0.9
    if sent.semantic_locked:
        try:
            assert_semantic_rewrite_allowed(
                sent,
                rewritten,
                meaning_similarity=max(0.85, meaning),
                entity_preservation=score,
            )
        except Exception:
            return sent
    sent.translated_text = rewritten
    return sent


def can_merge_pair(a: SemanticSentence, b: SemanticSentence) -> bool:
    """P12 — never merge different semantic blocks."""
    if a.speaker and b.speaker and a.speaker != b.speaker:
        return False
    if a.is_dialogue != b.is_dialogue and (a.is_dialogue or b.is_dialogue):
        return False
    # Gap too large → different blocks
    gap = b.start_ms - a.end_ms
    if gap > 1500:
        return False
    return True


def merge_sentences(
    sentences: list[SemanticSentence],
    *,
    max_merge: int = 3,
) -> list[SemanticSentence]:
    """P12 — merge up to 3 overflowing neighbors in same meaning block."""
    if not sentences:
        return []
    out: list[SemanticSentence] = []
    i = 0
    while i < len(sentences):
        cur = sentences[i]
        if cur.overflow_ms <= 0 or cur.slot_ms <= 0:
            out.append(cur)
            i += 1
            continue
        chain = [cur]
        j = i + 1
        while j < len(sentences) and len(chain) < max_merge:
            nxt = sentences[j]
            if not can_merge_pair(chain[-1], nxt):
                break
            # Prefer merging when next has spare (underflow) or also overflow
            chain.append(nxt)
            j += 1
            # Combined slot may absorb
            total_slot = chain[-1].end_ms - chain[0].start_ms
            total_pred = sum(
                (s.predicted_tts_ms or s.slot_ms) for s in chain
            )
            if total_pred <= int(total_slot * 1.10):
                break
        if len(chain) == 1:
            out.append(cur)
            i += 1
            continue
        merged = SemanticSentence(
            text=" ".join(s.text for s in chain),
            translated_text=" ".join(
                (s.translated_text or s.text) for s in chain
            ),
            words=[w for s in chain for w in s.words],
            start_ms=chain[0].start_ms,
            end_ms=chain[-1].end_ms,
            speaker=chain[0].speaker,
            entities=[e for s in chain for e in s.entities],
            verbs=[v for s in chain for v in s.verbs],
            semantic_locked=all(s.semantic_locked for s in chain),
            locked_entities=[e for s in chain for e in s.locked_entities],
            locked_numbers=[n for s in chain for n in s.locked_numbers],
            emotion=chain[0].emotion,
            recovery_plan=["sentence_merge"],
        )
        from engines.semantic_v3.timing_predictor import apply_audio_predictor

        apply_audio_predictor([merged])
        out.append(merged)
        i = j
    return out


def assign_dub_segments(sentences: list[SemanticSentence]) -> list[SemanticSentence]:
    """P13 — dub segment boundaries created AFTER translation (= one per sentence)."""
    import uuid

    for s in sentences:
        if not s.dub_segment_uuid:
            s.dub_segment_uuid = uuid.uuid4().hex
    return sentences


def run_adaptation_ladder(
    sentences: list[SemanticSentence],
    *,
    allow_rewrite: bool = True,
) -> list[SemanticSentence]:
    """P10 — fixed order; semantic rewrite last."""
    # Levels 1–7 are audio-side (Scheduler/ATO) — mark plan only here
    for s in sentences:
        if s.overflow_ms > 0 and not s.recovery_plan:
            s.recovery_plan = list(ADAPTATION_ORDER)
    # 8–9 sentence merge
    sentences = merge_sentences(sentences, max_merge=3)
    # 10 semantic rewrite
    if allow_rewrite:
        sentences = [try_semantic_rewrite_sentence(s) for s in sentences]
    return assign_dub_segments(sentences)
