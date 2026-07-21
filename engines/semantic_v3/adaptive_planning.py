"""P39 Adaptive Planning + P40 Decision Engine + P41 Dynamic Merge + P43 Rewrite 2.0."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.semantic_lock import (
    assert_semantic_rewrite_allowed,
    entity_preservation_score,
)
from engines.semantic_v3.types import SemanticSentence

DECISION_ORDER = (
    "trim_silence",
    "pause_optimization",
    "prosody",
    "tempo",
    "stretch",
    "borrow_time",
    "sentence_merge",
    "semantic_rewrite",
    "manual_review",
)

_REWRITE_V2: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bI am going to\b", re.I), "I'll"),
    (re.compile(r"\bI will\b", re.I), "I'll"),
    (re.compile(r"\bdo not\b", re.I), "don't"),
    (re.compile(r"\bdid not\b", re.I), "didn't"),
    (re.compile(r"\bcannot\b", re.I), "can't"),
    (re.compile(r"\bwould not\b", re.I), "wouldn't"),
    (re.compile(r"\bit is\b", re.I), "it's"),
    (re.compile(r"\bв зв'язку з тим, що\b", re.I), "тому що"),
    (re.compile(r"\bу той момент, коли\b", re.I), "коли"),
    (re.compile(r"\bпісля цього\b", re.I), "потім"),
    (re.compile(r"\bнадзвичайно\b", re.I), "дуже"),
    (re.compile(r"\bнасправді\b", re.I), ""),
]


@dataclass
class AdaptivePlan:
    sentence_uuid: str
    fits: bool
    expected_ms: int
    slot_ms: int
    overflow_ms: int
    decisions: list[str] = field(default_factory=list)
    tts_allowed: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def max_merge_config() -> int:
    """P41 — configurable merge limit (default 5, was hard 3)."""
    try:
        return max(2, min(12, int(os.environ.get("VM_SEMANTIC_MAX_MERGE", "5"))))
    except ValueError:
        return 5


def build_adaptive_plan(sent: SemanticSentence) -> AdaptivePlan:
    """P39 — plan before TTS; TTS without plan is forbidden."""
    slot = sent.slot_ms
    expected = int(sent.predicted_tts_ms or 0)
    overflow = max(0, expected - slot) if slot > 0 else 0
    fits = overflow <= int(slot * 0.08) if slot > 0 else True
    decisions: list[str] = []
    if not fits:
        # Walk P40 order until predicted fit
        for step in DECISION_ORDER:
            decisions.append(step)
            if step == "trim_silence":
                expected = int(expected * 0.97)
            elif step == "pause_optimization":
                expected = int(expected * 0.96)
            elif step == "prosody":
                expected = int(expected * 0.98)
            elif step == "tempo":
                expected = int(expected * 0.95)
            elif step == "stretch":
                expected = int(expected * 0.97)
            elif step == "borrow_time":
                expected = max(slot, expected - min(400, overflow // 2))
            elif step in ("sentence_merge", "semantic_rewrite"):
                break
            if expected <= int(slot * 1.08):
                fits = True
                break
    tts_allowed = True
    reason = "fits" if fits else "needs_adaptation"
    if slot > 0 and expected > int(slot * 1.5) and "manual_review" not in decisions:
        decisions.append("manual_review")
        # Still allow TTS after plan — extreme cases go to studio
        reason = "extreme_overflow_manual_review"
    plan = AdaptivePlan(
        sentence_uuid=sent.sentence_uuid,
        fits=fits,
        expected_ms=expected,
        slot_ms=slot,
        overflow_ms=max(0, expected - slot) if slot else 0,
        decisions=decisions,
        tts_allowed=tts_allowed,
        reason=reason,
    )
    setattr(sent, "adaptive_plan", plan)
    sent.recovery_plan = list(decisions)
    return plan


def plan_all(sentences: list[SemanticSentence]) -> list[AdaptivePlan]:
    return [build_adaptive_plan(s) for s in sentences]


def assert_tts_planned(sent: SemanticSentence) -> None:
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    plan = getattr(sent, "adaptive_plan", None)
    if plan is None:
        raise ArchitectureViolation(
            "P39: TTS forbidden without AdaptivePlan",
            stage="adaptive_planning",
            rule="no_tts_without_plan",
            segment_id=sent.sentence_uuid,
        )


def semantic_rewrite_v2(text: str) -> str:
    """P43 — localization adaptation, not mechanical chopping."""
    out = " ".join(str(text or "").split())
    for pat, repl in _REWRITE_V2:
        out = pat.sub(repl, out)
    return " ".join(out.split())


def try_rewrite_v2(sent: SemanticSentence) -> SemanticSentence:
    src = sent.translated_text or sent.text
    rewritten = semantic_rewrite_v2(src)
    if rewritten == src:
        return sent
    ent = entity_preservation_score(sent, rewritten)
    # Numbers / names check
    import re

    nums = re.findall(r"\d[\d.,]*", src)
    for n in nums:
        if n not in rewritten:
            return sent
    meaning = 0.92 if len(rewritten) >= int(len(src) * 0.55) else 0.7
    if sent.semantic_locked:
        try:
            assert_semantic_rewrite_allowed(
                sent,
                rewritten,
                meaning_similarity=meaning,
                entity_preservation=ent,
                threshold=0.85,
            )
        except Exception:
            return sent
    if ent < 1.0:
        return sent
    sent.translated_text = rewritten
    return sent


def can_merge_pair(a: SemanticSentence, b: SemanticSentence) -> bool:
    if a.speaker and b.speaker and a.speaker != b.speaker:
        return False
    if a.is_dialogue != b.is_dialogue and (a.is_dialogue or b.is_dialogue):
        return False
    if b.start_ms - a.end_ms > 1500:
        return False
    # Different semantic blocks: conflicting primary entities + large gap
    if a.entities and b.entities:
        if not set(x.lower() for x in a.entities) & set(x.lower() for x in b.entities):
            if b.start_ms - a.end_ms > 600:
                return False
    return True


def dynamic_sentence_merge(
    sentences: list[SemanticSentence],
    *,
    max_merge: int | None = None,
) -> list[SemanticSentence]:
    """P41 — merge count from config + logic, not hard ≤3."""
    limit = max_merge if max_merge is not None else max_merge_config()
    if not sentences:
        return []
    out: list[SemanticSentence] = []
    i = 0
    while i < len(sentences):
        cur = sentences[i]
        plan = getattr(cur, "adaptive_plan", None)
        need_merge = (cur.overflow_ms > 0) or (
            plan and "sentence_merge" in (plan.decisions if plan else [])
        )
        if not need_merge:
            out.append(cur)
            i += 1
            continue
        chain = [cur]
        j = i + 1
        while j < len(sentences) and len(chain) < limit:
            nxt = sentences[j]
            if not can_merge_pair(chain[-1], nxt):
                break
            chain.append(nxt)
            j += 1
            total_slot = chain[-1].end_ms - chain[0].start_ms
            total_pred = sum(int(s.predicted_tts_ms or s.slot_ms) for s in chain)
            if total_pred <= int(total_slot * 1.10):
                break
        if len(chain) == 1:
            out.append(cur)
            i += 1
            continue
        merged = SemanticSentence(
            text=" ".join(s.text for s in chain),
            translated_text=" ".join((s.translated_text or s.text) for s in chain),
            words=[w for s in chain for w in s.words],
            start_ms=chain[0].start_ms,
            end_ms=chain[-1].end_ms,
            speaker=chain[0].speaker,
            entities=[e for s in chain for e in s.entities],
            verbs=[v for s in chain for v in s.verbs],
            semantic_locked=all(s.semantic_locked for s in chain),
            locked_entities=[e for s in chain for e in (s.locked_entities or s.entities)],
            locked_numbers=[n for s in chain for n in s.locked_numbers],
            emotion=chain[0].emotion,
            recovery_plan=["sentence_merge"],
        )
        out.append(merged)
        i = j
    return out
