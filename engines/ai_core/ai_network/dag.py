"""AI Network DAG — agent dependencies and execution order (TZ Stage 10)."""

from __future__ import annotations

from typing import Any

# Strict DAG matching AICoreOrchestrator.AGENT_CHAIN (no cycles).
DEFAULT_DAG: list[tuple[str, tuple[str, ...]]] = [
    ("planner", ()),
    ("stt", ("planner",)),
    ("director", ("stt",)),
    ("translation", ("director",)),
    ("semantic", ("translation",)),
    ("timing", ("semantic",)),
    ("grammar", ("timing",)),
    ("quality", ("grammar",)),
    ("reviewer", ("quality",)),
    ("voice_preparation", ("reviewer",)),
    ("voice", ("voice_preparation",)),
    ("voice_verification", ("voice",)),
    ("mix", ("voice_verification",)),
]

STREAMING_TEXT_BLOCK = (
    "translation",
    "semantic",
    "timing",
    "grammar",
    "quality",
    "reviewer",
)


def validate_dag(edges: list[tuple[str, tuple[str, ...]]] | None = None) -> bool:
    """Return False if cyclic dependencies detected."""
    graph = dict(edges or DEFAULT_DAG)
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        for dep in graph.get(node, ()):
            if dep in graph and not dfs(dep):
                return False
        visiting.remove(node)
        visited.add(node)
        return True

    return all(dfs(n) for n in graph)


def get_execution_order(
    *,
    streaming: bool = False,
    skip: set[str] | None = None,
    agents_filter: list[str] | None = None,
) -> list[str]:
    """Topological order for orchestrator — future AI Network routing."""
    skip_set = set(skip or ())
    order = [name for name, _ in DEFAULT_DAG if name not in skip_set]
    if streaming:
        first = order.index("translation")
        last = order.index("reviewer")
        order = order[:first] + ["streaming_text"] + order[last + 1 :]
    if agents_filter:
        allowed = set(agents_filter)
        order = [n for n in order if n in allowed]
    return order


def dag_snapshot(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Serializable DAG for diagnostics."""
    state = state or {}
    return {
        "valid": validate_dag(),
        "nodes": [n for n, _ in DEFAULT_DAG],
        "edges": {n: list(deps) for n, deps in DEFAULT_DAG},
        "execution_order": get_execution_order(
            streaming=str(state.get("pipeline_mode") or "") == "streaming",
        ),
        "streaming_block": list(STREAMING_TEXT_BLOCK),
    }
