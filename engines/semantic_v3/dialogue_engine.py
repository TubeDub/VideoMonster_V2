"""P109 — Dialogue Engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence


@dataclass
class DialogueTurn:
    sentence_uuid: str
    speaker: str
    text: str
    is_question: bool = False
    is_answer: bool = False
    emotion: str = "neutral"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Dialogue:
    dialogue_id: str
    participants: list[str] = field(default_factory=list)
    turns: list[DialogueTurn] = field(default_factory=list)
    emotion_arc: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dialogue_id": self.dialogue_id,
            "participants": list(self.participants),
            "turns": [t.to_dict() for t in self.turns],
            "emotion_arc": list(self.emotion_arc),
        }


def build_dialogues(sentences: list[SemanticSentence]) -> list[Dialogue]:
    """Group consecutive dialogue / Q-A sentences into conversations."""
    dialogues: list[Dialogue] = []
    current: Dialogue | None = None
    prev_question = False

    for s in sentences:
        is_dlg = bool(
            s.is_dialogue
            or s.is_direct_speech
            or s.is_question
            or (s.speaker and len(s.words) <= 16)
        )
        if not is_dlg:
            current = None
            prev_question = False
            continue

        if current is None:
            dlg_id = f"dlg_{s.sentence_uuid[:10]}"
            current = Dialogue(dialogue_id=dlg_id)
            dialogues.append(current)

        s.dialogue_id = current.dialogue_id
        s.is_dialogue = True
        speaker = s.speaker or "SPEAKER_A"
        if speaker not in current.participants:
            current.participants.append(speaker)

        turn = DialogueTurn(
            sentence_uuid=s.sentence_uuid,
            speaker=speaker,
            text=s.text,
            is_question=s.is_question or s.text.strip().endswith("?"),
            is_answer=prev_question and not (s.is_question or s.text.strip().endswith("?")),
            emotion=s.emotion or "neutral",
        )
        current.turns.append(turn)
        current.emotion_arc.append(turn.emotion)
        prev_question = turn.is_question

        # Speaker change detection via relation tag
        if len(current.turns) >= 2:
            if current.turns[-2].speaker != turn.speaker:
                s.relations = list(
                    dict.fromkeys([*(s.relations or []), "dialogue:speaker_change"])
                )
        if turn.is_answer:
            s.relations = list(
                dict.fromkeys([*(s.relations or []), "dialogue:answer"])
            )
    return dialogues
