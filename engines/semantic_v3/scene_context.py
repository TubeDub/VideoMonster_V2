"""P111 — Scene Context."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence


@dataclass
class SceneContext:
    scene_uuid: str
    goal: str = ""
    emotion: str = "neutral"
    events_before: list[str] = field(default_factory=list)
    events_after: list[str] = field(default_factory=list)
    sentence_uuids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assign_scenes(
    sentences: list[SemanticSentence],
    *,
    gap_ms: int = 8000,
) -> list[SceneContext]:
    """Split project into scenes by long pauses / topic breaks."""
    if not sentences:
        return []
    scenes: list[SceneContext] = []
    current = SceneContext(scene_uuid=uuid.uuid4().hex)
    scenes.append(current)

    for i, s in enumerate(sentences):
        if i > 0:
            gap = s.start_ms - sentences[i - 1].end_ms
            topic_break = (
                bool(s.entities)
                and bool(sentences[i - 1].entities)
                and not set(x.lower() for x in s.entities)
                & set(x.lower() for x in sentences[i - 1].entities)
                and gap > 2500
            )
            if gap >= gap_ms or topic_break:
                current = SceneContext(scene_uuid=uuid.uuid4().hex)
                scenes.append(current)

        s.scene_uuid = current.scene_uuid
        for w in s.words:
            w.scene_uuid = current.scene_uuid
        current.sentence_uuids.append(s.sentence_uuid)
        if s.emotion and s.emotion != "neutral":
            current.emotion = s.emotion
        if s.intent:
            current.goal = s.intent

    # Fill before/after events
    for si, scene in enumerate(scenes):
        if si > 0:
            prev = scenes[si - 1]
            scene.events_before = [
                sentences[j].text[:80]
                for j, sent in enumerate(sentences)
                if sent.sentence_uuid in prev.sentence_uuids[-2:]
            ]
        if si + 1 < len(scenes):
            nxt = scenes[si + 1]
            scene.events_after = [
                sentences[j].text[:80]
                for j, sent in enumerate(sentences)
                if sent.sentence_uuid in nxt.sentence_uuids[:2]
            ]
        for s in sentences:
            if s.sentence_uuid in scene.sentence_uuids:
                s.context = {
                    **(s.context or {}),
                    "scene_uuid": scene.scene_uuid,
                    "scene_goal": scene.goal,
                    "scene_emotion": scene.emotion,
                    "events_before": list(scene.events_before),
                    "events_after": list(scene.events_after),
                }
    return scenes
