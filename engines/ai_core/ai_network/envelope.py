"""AI Network message envelope (TZ #1 §3)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


EVENT_PIPELINE_STARTED = "pipeline_started"
EVENT_PIPELINE_FINISHED = "pipeline_finished"
EVENT_AGENT_STARTED = "agent_started"
EVENT_AGENT_FINISHED = "agent_finished"
EVENT_SEGMENT_IN = "segment_in"
EVENT_SEGMENT_OUT = "segment_out"
EVENT_REVIEWER_APPROVED = "reviewer_approved"
EVENT_REVIEWER_REJECTED = "reviewer_rejected"
EVENT_RECOVERY_ACTION = "recovery_action"
EVENT_SKILL_VIOLATION = "skill_violation"


@dataclass
class NetworkEnvelope:
    """Single message on the AI Network bus."""

    event: str
    source: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "event": self.event,
            "source": self.source,
            "run_id": self.run_id,
            "ts": self.ts,
            "payload": self.payload,
        }
