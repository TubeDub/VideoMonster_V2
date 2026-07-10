"""AI Core 3.0 — shared contracts for planner and downstream agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AgentExecutionResult:
    """Standard return type for AI Core agents."""

    status: str  # success|warning|error
    updated_state: dict
    metrics: dict
    warnings: list
    errors: list
    execution_time_ms: float
    decision_log: list

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_protocol(self, agent_id: str, agent_version: str) -> dict[str, Any]:
        """Attach Master Spec §21 version block to serialized result."""
        from engines.ai_core.platform.versions import agent_protocol_header

        payload = self.to_dict()
        payload["protocol"] = agent_protocol_header(agent_id, agent_version)
        return payload


@dataclass
class PipelineResult:
    """Result of AICoreOrchestrator.run_pipeline."""

    status: str  # success|warning|error|critical_error
    updated_state: dict
    agent_results: dict
    warnings: list
    errors: list
    critical: bool = False
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectManifest:
    """TubeDub Planner Agent v3.0 project manifest (spec §5)."""

    project_uuid: str
    pipeline_version: str
    manifest_version: str = "3.0"
    protocol_versions: dict = field(default_factory=dict)
    ai_core_version: str = "3.0"
    planner_version: str = "3.0"
    task_id: str = ""
    video_path: str = ""
    target_lang: str = ""
    source_lang: str = ""
    created_at: str = ""

    # Pre-flight analysis
    video_exists: bool = False
    audio_track_count: int = 0
    duration_ms: int = 0
    segment_count_estimate: int = 0
    language_hint: str = ""
    content_type: str = "mixed"  # speech|music|mixed
    music_detected: bool = False
    noise_level: str = "low"  # low|medium|high
    audio_quality_score: float = 0.0

    # Capability & smoke
    capability_matrix: dict = field(default_factory=dict)
    smoke_tests: dict = field(default_factory=dict)

    # Decisions
    confidence_scores: dict = field(default_factory=dict)
    complexity_score: str = "LOW"  # LOW|MEDIUM|HIGH|EXTREME
    processing_strategy: str = "BALANCED"
    resource_estimation: dict = field(default_factory=dict)
    decision_reasons: dict = field(default_factory=dict)

    # Agent orchestration
    agent_dependencies: dict = field(default_factory=dict)
    success_criteria: dict = field(default_factory=dict)
    fallback_map: dict = field(default_factory=dict)
    agent_capabilities: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
