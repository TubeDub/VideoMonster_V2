"""P103 — MeaningUnit builder.

Groups SemanticSentence objects into MeaningUnit objects based on
thought-completeness heuristics.
"""

from __future__ import annotations

import re
from typing import Any

from engines.semantic_v3.types import MeaningUnit, SemanticSentence, SemanticWord

_CONJUNCTIONS = frozenset({
    "and", "but", "or", "nor", "yet", "so", "because", "although", "though",
    "however", "moreover", "furthermore", "meanwhile", "nevertheless",
    "и", "а", "но", "или", "однако", "потому", "поэтому", "также",
    "причём", "причем", "притом", "зато", "да", "ведь",
})

_SPLIT_CONJUNCTIONS = frozenset({
    "and", "but", "however", "yet", "meanwhile", "nevertheless",
    "и", "а", "но", "однако", "зато",
})

_MAX_MERGE_PAUSE_MS = 500
_LONG_SENTENCE_WORD_THRESHOLD = 30


def _starts_with_lowercase_or_conjunction(text: str) -> bool:
    if not text:
        return False
    first_word = text.split()[0] if text.split() else ""
    if first_word.lower() in _CONJUNCTIONS:
        return True
    return text[0].islower()


def _pause_between(prev: SemanticSentence, curr: SemanticSentence) -> int:
    if prev.end_ms <= 0 or curr.start_ms <= 0:
        return 0
    return max(0, curr.start_ms - prev.end_ms)


def _is_dialogue(sentence: SemanticSentence) -> bool:
    return sentence.is_dialogue or sentence.is_direct_speech


def _should_merge(prev: SemanticSentence, curr: SemanticSentence) -> bool:
    """Decide whether two adjacent sentences should be merged into one MeaningUnit."""
    if _is_dialogue(prev) != _is_dialogue(curr):
        return False

    if prev.speaker and curr.speaker and prev.speaker != curr.speaker:
        return False

    pause = _pause_between(prev, curr)
    if pause >= _MAX_MERGE_PAUSE_MS:
        return False

    if _starts_with_lowercase_or_conjunction(curr.text):
        return True

    if prev.is_question and curr.speaker == prev.speaker and pause < _MAX_MERGE_PAUSE_MS:
        return True

    return False


def _find_split_points(sentence: SemanticSentence) -> list[int]:
    """Find word indices where a long sentence can be split into independent clauses.

    Returns indices of words *after* which to split (the split word is the last
    word of the first resulting clause).
    """
    words = sentence.words
    if len(words) <= _LONG_SENTENCE_WORD_THRESHOLD:
        return []

    if ";" in sentence.text:
        split_indices = []
        for i, w in enumerate(words):
            if w.punctuation == ";" or w.text.endswith(";"):
                if i > 0 and i < len(words) - 1:
                    split_indices.append(i)
        if split_indices:
            return split_indices

    split_indices = []
    for i, w in enumerate(words):
        if i < 5 or i > len(words) - 5:
            continue
        if w.text.lower().rstrip(".,;:") in _SPLIT_CONJUNCTIONS:
            split_indices.append(i - 1)

    if not split_indices:
        return []

    filtered: list[int] = []
    for idx in split_indices:
        left_count = (idx + 1) if not filtered else (idx - filtered[-1])
        if left_count >= 8:
            filtered.append(idx)

    if filtered and (len(words) - 1 - filtered[-1]) < 8:
        filtered.pop()

    return filtered


def _split_sentence_into_units(sentence: SemanticSentence) -> list[MeaningUnit]:
    """Split a long sentence into multiple MeaningUnits at clause boundaries."""
    split_points = _find_split_points(sentence)
    if not split_points:
        return [MeaningUnit(sentences=[sentence])]

    units: list[MeaningUnit] = []
    words = sentence.words
    prev_start = 0

    for split_idx in split_points:
        chunk_words = words[prev_start : split_idx + 1]
        if not chunk_words:
            continue
        sub_sentence = SemanticSentence(
            text=" ".join(w.text for w in chunk_words),
            words=list(chunk_words),
            start_ms=chunk_words[0].start_ms,
            end_ms=chunk_words[-1].end_ms,
            speaker=sentence.speaker,
            emotion=sentence.emotion,
            is_dialogue=sentence.is_dialogue,
            is_direct_speech=sentence.is_direct_speech,
            dialogue_id=sentence.dialogue_id,
            scene_uuid=sentence.scene_uuid,
            style=sentence.style,
        )
        units.append(MeaningUnit(
            sentences=[sub_sentence],
            meaning_complete=False,
        ))
        prev_start = split_idx + 1

    remaining_words = words[prev_start:]
    if remaining_words:
        sub_sentence = SemanticSentence(
            text=" ".join(w.text for w in remaining_words),
            words=list(remaining_words),
            start_ms=remaining_words[0].start_ms,
            end_ms=remaining_words[-1].end_ms,
            speaker=sentence.speaker,
            emotion=sentence.emotion,
            is_dialogue=sentence.is_dialogue,
            is_direct_speech=sentence.is_direct_speech,
            dialogue_id=sentence.dialogue_id,
            scene_uuid=sentence.scene_uuid,
            style=sentence.style,
        )
        units.append(MeaningUnit(sentences=[sub_sentence]))

    return units


def _inherit_sentence_metadata(unit: MeaningUnit) -> None:
    """Copy metadata from the first sentence into the MeaningUnit."""
    if not unit.sentences:
        return
    first = unit.sentences[0]
    if not unit.emotion or unit.emotion == "neutral":
        unit.emotion = first.emotion
    if not unit.speaker:
        unit.speaker = first.speaker
    if first.dialogue_id:
        if first.dialogue_id not in unit.dialogue_history:
            unit.dialogue_history.append(first.dialogue_id)


def build_meaning_units(sentences: list[SemanticSentence]) -> list[MeaningUnit]:
    """P103 — group sentences into MeaningUnit objects.

    Grouping rules:
    1. Most sentences map 1:1 to a MeaningUnit.
    2. Merge adjacent sentences when they share a speaker, the second continues
       a thought (lowercase start or conjunction), and the pause is < 500 ms.
    3. Split long sentences (>30 words) with independent clauses at semicolons
       or coordinating conjunctions.
    4. Never split dialogue turns between MeaningUnits.
    5. Keep questions and answer context together for same speaker.
    """
    if not sentences:
        return []

    raw_units: list[MeaningUnit] = []

    for sent in sentences:
        if not sent.text and not sent.words:
            continue

        if len(sent.words) > _LONG_SENTENCE_WORD_THRESHOLD and not _is_dialogue(sent):
            split_results = _split_sentence_into_units(sent)
            raw_units.extend(split_results)
        else:
            raw_units.append(MeaningUnit(sentences=[sent]))

    if not raw_units:
        return []

    merged: list[MeaningUnit] = [raw_units[0]]

    for unit in raw_units[1:]:
        prev_unit = merged[-1]
        prev_last_sentence = prev_unit.sentences[-1] if prev_unit.sentences else None
        curr_first_sentence = unit.sentences[0] if unit.sentences else None

        if (
            prev_last_sentence
            and curr_first_sentence
            and _should_merge(prev_last_sentence, curr_first_sentence)
        ):
            prev_unit.sentences.extend(unit.sentences)
            prev_unit.text = " ".join(s.text for s in prev_unit.sentences if s.text)
            prev_unit.end_ms = prev_unit.sentences[-1].end_ms
            prev_unit.sentence_count = len(prev_unit.sentences)
            prev_unit.word_count = sum(len(s.words) for s in prev_unit.sentences)
        else:
            merged.append(unit)

    for unit in merged:
        _inherit_sentence_metadata(unit)

    for i, unit in enumerate(merged):
        if i > 0:
            unit.prev_unit_uuid = merged[i - 1].unit_uuid
        if i < len(merged) - 1:
            unit.next_unit_uuid = merged[i + 1].unit_uuid

    return merged
