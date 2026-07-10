"""Agent protocol mixin — versioning + Global Skill for all agents (§21, §25)."""

from __future__ import annotations

from typing import Any

from engines.ai_core.global_skill import augment_system_prompt, skill_version
from engines.ai_core.platform.versions import agent_protocol_header


class AgentProtocolMixin:
    """Mixin for AI Core agents — declare versions and inherit Global Skill."""

    agent_id: str = "agent"
    agent_version: str = "1.0"

    def protocol_meta(self) -> dict[str, Any]:
        return agent_protocol_header(self.agent_id, self.agent_version)

    def global_skill_version(self) -> str:
        return skill_version()

    def augment_llm_system(self, system: str | None) -> str:
        return augment_system_prompt(system)

    def execution_metrics(
        self,
        *,
        ms: float,
        retries: int = 0,
        processed: int = 0,
        rejected: int = 0,
        quality_score: float | None = None,
    ) -> dict[str, Any]:
        """Observability block (§17, §22)."""
        return {
            **self.protocol_meta(),
            "execution_time_ms": round(ms, 1),
            "retry_count": retries,
            "processed_segments": processed,
            "rejected_segments": rejected,
            "quality_score": quality_score,
            "global_skill_version": self.global_skill_version(),
        }
