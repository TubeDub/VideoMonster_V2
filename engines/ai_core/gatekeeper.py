"""Gatekeeper — dependency and success gates for AI Core agents (future use)."""

from __future__ import annotations

from typing import Any


class DependencyGate:
    """Verify required capabilities before an agent may run."""

    def __init__(self, capability_matrix: dict[str, Any]):
        self._cap = capability_matrix

    def check(self, requirements: dict[str, bool]) -> tuple[bool, list[str]]:
        missing: list[str] = []
        for key, required in requirements.items():
            if not required:
                continue
            if not self._cap.get(key):
                missing.append(key)
        return len(missing) == 0, missing


class SuccessGate:
    """Evaluate agent success criteria against a result payload."""

    def evaluate(self, criteria: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
        failures: list[str] = []
        for key, expected in criteria.items():
            actual = result.get(key)
            if isinstance(expected, bool):
                if bool(actual) != expected:
                    failures.append(key)
            elif actual is None:
                failures.append(key)
            elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
                if actual < expected:
                    failures.append(key)
        return len(failures) == 0, failures


def default_agent_dependencies() -> dict[str, list[str]]:
    """DAG: agent → list of prerequisite agents."""
    return {
        "planner": [],
        "extract_audio": ["planner"],
        "stt": ["extract_audio"],
        "translate": ["stt"],
        "tts": ["translate"],
        "timing": ["tts"],
        "dub": ["timing"],
    }


def default_success_criteria() -> dict[str, dict[str, Any]]:
    return {
        "planner": {"status": "success"},
        "stt": {"segments_min": 1},
        "translate": {"segments_min": 1},
        "tts": {"audio_generated": True},
        "dub": {"output_exists": True},
    }


def default_fallback_map() -> dict[str, str]:
    return {
        "llm": "rule_based_adaptation",
        "tts": "edge_tts",
        "stt": "smaller_whisper_model",
        "translate": "mt_engine",
    }
