"""AI Core 3.0 — Agent framework (base interfaces).

AI Core is now a *coordinator*: it decides which agents run and in what order.
Each agent does exactly ONE job and nothing else. This module defines the small
common contract every agent obeys so the coordinator can drive an ordered
pipeline and future modules (Dub Studio, Reader, Voice Studio) can add or reuse
agents without rewriting AI Core.

Key types:

* :class:`SegmentContext` — the mutable per-segment working state that flows
  down the chain (each agent receives the already-processed result of the
  previous one).
* :class:`AgentResult` — what each agent hands the next: result text + quality
  score + reason for changes + diagnostics (+ optional ``route_back_to`` for the
  Quality Agent to return work to a single responsible agent).
* :class:`Agent` — the ABC every agent implements: ``name``, ``needed(ctx)``
  (cheap "is this agent even required?" gate) and ``run(ctx) -> AgentResult``.
* :class:`AgentCache` — a namespaced view over :mod:`engines.llm_cache` so each
  agent has its OWN cache keyed by (agent, original text, language, settings,
  model, quality mode) and never regenerates a result.

Design rules carried over from the P0 no-hang work:
* Agents must try a cheap / rule-based path FIRST and only use the LLM (always
  via :func:`engines.ai_core.llm_gateway.chat`) when that is impossible.
* Agents never block unbounded — the coordinator wraps each segment's whole
  chain in the wall-clock watchdog and the LLM gateway keeps the finite timeout,
  concurrency semaphore, run-wide circuit breaker and endpoint cache.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from engines import llm_cache

# Bump to invalidate every agent cache entry at once.
AGENT_CACHE_VERSION = "ai_agents_v1"


@dataclass
class SegmentContext:
    """Mutable per-segment state that flows through the agent chain.

    ``text`` is the *current* working translation and is updated in place by
    each agent that changes it. ``raw_translation`` keeps the faithful literal
    translation the Translation Agent produced (used as a safe fallback so we
    never emit an empty / English-leaking segment).
    """

    index: int
    source_text: str = ""          # Original (source-language) line — the truth.
    raw_translation: str = ""      # Faithful literal translation (Translation Agent).
    text: str = ""                 # Current working text (evolves through chain).
    slot_ms: int = 0
    src_lang: str = ""
    tgt_lang: str = ""
    task_id: str = ""

    # Project-wide decisions from the Planner (read-only for other agents).
    profile: dict[str, Any] = field(default_factory=dict)
    strategy: dict[str, Any] = field(default_factory=dict)

    # Accumulated outputs / diagnostics.
    voice: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    # The Timing Agent's TimingAwareRecord (kept so the coordinator can emit a
    # pipeline-compatible record without re-measuring).
    timing_record: Any = None

    @property
    def model(self) -> str:
        return str(self.strategy.get("model") or "")

    @property
    def quality_mode(self) -> str:
        return str(self.strategy.get("speed_mode") or "balanced")

    @property
    def llm_policy(self) -> str:
        return str(self.strategy.get("llm_policy") or "problem_only")

    def allow_llm(self) -> bool:
        """LLM is a TOOL agents may use only when their cheap path fails."""
        return bool(self.strategy.get("use_llm", True)) and self.llm_policy != "off"


@dataclass
class AgentResult:
    """What one agent hands to the next (result + score + reason + diagnostics)."""

    agent: str
    text: str
    changed: bool = False
    ok: bool = True
    quality_score: float = 1.0
    reason: str = ""
    used_llm: bool = False
    cache_hit: bool = False
    attempts: int = 1
    skipped: bool = False
    elapsed_ms: float = 0.0
    # When set (by the Quality Agent), the coordinator must return work to ONLY
    # this agent instead of restarting the whole chain.
    route_back_to: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_timeline_row(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "changed": self.changed,
            "ok": self.ok,
            "skipped": self.skipped,
            "quality_score": round(float(self.quality_score), 3),
            "reason": self.reason,
            "model": self.diagnostics.get("model", ""),
            "used_llm": self.used_llm,
            "cache_hit": self.cache_hit,
            "attempts": self.attempts,
            "time_ms": round(float(self.elapsed_ms), 1),
            "route_back_to": self.route_back_to,
            "input_data": self.diagnostics.get("input_data"),
            "output_data": self.diagnostics.get("output_data"),
            "diagnostics": self.diagnostics,
        }


class AgentCache:
    """Per-agent namespaced cache over :mod:`engines.llm_cache`.

    Each agent gets its own key space so results are never regenerated and never
    collide across agents. Values are JSON-serialisable dicts.
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = str(namespace)

    def key(self, *parts: Any) -> str:
        return llm_cache.make_key(AGENT_CACHE_VERSION, self.namespace, *parts)

    def get(self, key: str) -> dict[str, Any] | None:
        raw = llm_cache.get(key)
        if not raw:
            return None
        try:
            import json

            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        try:
            import json

            llm_cache.put(key, json.dumps(value, ensure_ascii=False))
        except Exception:
            pass


class Agent(ABC):
    """Common contract for every agent. One agent = one job."""

    #: Stable machine name used in the timeline, cache namespace and routing.
    name: str = "agent"

    def __init__(self) -> None:
        self.cache = AgentCache(self.name)

    def needed(self, ctx: SegmentContext) -> bool:
        """Cheap gate: is this agent required for this segment at all?

        Returning ``False`` lets the coordinator skip the agent entirely (e.g. a
        segment that already fits skips the Timing rewrite; perfect grammar skips
        the Grammar Agent). Default: always needed.
        """
        return True

    @abstractmethod
    def _run(self, ctx: SegmentContext) -> AgentResult:
        """Do the one job. Implementations must be bounded and never block."""

    def run(self, ctx: SegmentContext) -> AgentResult:
        """Public entry: times the agent and guarantees a non-crashing result."""
        t0 = time.perf_counter()
        try:
            result = self._run(ctx)
        except Exception as exc:  # pragma: no cover - defensive, never crash chain
            result = AgentResult(
                agent=self.name,
                text=ctx.text,
                ok=True,
                changed=False,
                reason=f"agent_error:{type(exc).__name__}",
            )
        result.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return result

    # ── shared helpers ───────────────────────────────────────────────────
    def _skip(self, ctx: SegmentContext, reason: str) -> AgentResult:
        return AgentResult(
            agent=self.name, text=ctx.text, changed=False, skipped=True, reason=reason
        )


class BaseAgent(Agent):
    """Coordinator-path unified base (TZ Stage 1) — extends Agent ABC.

    New coordinator agents should inherit from BaseAgent.
    Orchestrator-path agents use :class:`engines.ai_core.orchestrator_agent_base.OrchestratorAgentBase`.
    """

    agent_version: str = "1.0"

    def protocol_meta(self) -> dict:
        try:
            from engines.ai_core.platform.agent_protocol import AgentProtocolMixin

            return AgentProtocolMixin.protocol_meta(self)  # type: ignore[arg-type]
        except Exception:
            return {"agent_id": self.name, "agent_version": self.agent_version}
