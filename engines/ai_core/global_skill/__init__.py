"""TubeDub Global Skill — architectural rules for every AI agent."""

from engines.ai_core.global_skill.skill import (
    augment_system_prompt,
    check_agent_result,
    load_skill,
    principles,
    rule_ids,
    skill_version,
    to_dict,
)

__all__ = [
    "augment_system_prompt",
    "check_agent_result",
    "load_skill",
    "principles",
    "rule_ids",
    "skill_version",
    "to_dict",
]
