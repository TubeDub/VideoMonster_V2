"""Post-agent Reviewer gate — Global Skill compliance (TZ #1 §2)."""

from __future__ import annotations

import logging
from typing import Any

from engines.ai_core.global_skill import check_agent_result

logger = logging.getLogger("tubedub.ai_core.reviewer_gate")


def review_agent_output(
    run_id: str,
    agent_name: str,
    *,
    status: str = "success",
    segments: list[dict[str, Any]] | None = None,
    tgt_lang: str = "",
    errors: list[str] | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Lightweight automatic review after any agent finishes."""
    result = check_agent_result(
        agent_name,
        status=status,
        segments=segments,
        tgt_lang=tgt_lang,
        errors=errors,
    )
    if not publish or not run_id:
        return result

    from engines.ai_core.ai_event_log import reviewer_approved, reviewer_rejected
    from engines.ai_core.ai_network.bridge import emit_skill_violation
    from engines.ai_core.ai_network.bus import get_network
    from engines.ai_core.ai_network.envelope import (
        EVENT_REVIEWER_APPROVED,
        EVENT_REVIEWER_REJECTED,
    )

    net = get_network(run_id)
    if result.get("approved"):
        reviewer_approved(run_id, agent_name)
        net.publish(EVENT_REVIEWER_APPROVED, "reviewer_gate", result)
    else:
        reason = "; ".join(
            v.get("message", "") for v in result.get("violations") or []
        )[:200]
        reviewer_rejected(run_id, agent_name, reason=reason)
        net.publish(EVENT_REVIEWER_REJECTED, "reviewer_gate", result)
        for v in result.get("violations") or []:
            emit_skill_violation(run_id, v)

    return result
