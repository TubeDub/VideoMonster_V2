"""Orchestrator-path agent base (TZ Stage 1) — protocol + data contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.platform.agent_protocol import AgentProtocolMixin


class OrchestratorAgentBase(AgentProtocolMixin, ABC):
    """Base for manifest/state agents — returns AgentExecutionResult (TZ Stage 17)."""

    agent_id: str = "agent"
    agent_version: str = "1.0"

    @abstractmethod
    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        """Execute agent — manifest is read-only, state is mutable within scope."""

    def to_result(
        self,
        *,
        status: str,
        state: dict[str, Any],
        warnings: list | None = None,
        errors: list | None = None,
        metrics: dict | None = None,
        decision_log: list | None = None,
        execution_time_ms: float = 0.0,
    ) -> AgentExecutionResult:
        result = AgentExecutionResult(
            status=status,
            updated_state=state,
            metrics={**(metrics or {}), **self.protocol_meta()},
            warnings=list(warnings or []),
            errors=list(errors or []),
            execution_time_ms=execution_time_ms,
            decision_log=list(decision_log or []),
        )
        return result
