"""AI Core — the single intelligent core of TubeDub.

AI Core is the "director": it analyses the whole project, decides a strategy,
and drives the executors (adaptation engine, LLM gateway, voice director). It is
the single point of decision-making and the single owner of LLM traffic.

Typical use inside the pipeline::

    core = get_ai_core(task_id)
    profile = core.analyze(source_segments=..., translated_segments=...,
                           timing_map=..., src_lang=..., tgt_lang=...,
                           content_mode_hint=...)
    strategy = core.plan(requested_mode=user_mode)   # applies budget + profile
    segments, records = core.adapt_segments(segments, timing_map, sources,
                                            src_lang=..., tgt_lang=...,
                                            progress_cb=...)
    report = core.report()

All future modules (AutoDub, Translator, Reader, Dub/Voice Studio, AI Assistant)
should obtain AI Core via ``get_ai_core`` and go through it rather than calling
services directly.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Sequence

from engines.ai_core import llm_gateway
from engines.ai_core.project_analysis import ProjectProfile, analyze_project
from engines.ai_core.strategy import ProjectStrategy, build_strategy
from engines.ai_core.voice_director import VoiceDirection, decide_voice

logger = logging.getLogger("tubedub.ai_core")


def _agents_enabled() -> bool:
    """AI Core 3.0 multi-agent coordinator toggle (default ON, fail-safe)."""
    val = str(os.getenv("VM_AI_AGENTS", "1") or "1").strip().lower()
    return val not in ("0", "false", "no", "off", "")


class AICore:
    """Single decision-making core for one dub project."""

    def __init__(self, task_id: str = "") -> None:
        self.task_id = str(task_id or "")
        self.profile: ProjectProfile | None = None
        self.strategy: ProjectStrategy | None = None
        self._planner_agent = None
        self._planner_inputs: dict[str, Any] = {}
        self._agent_report: dict[str, Any] = {
            "planner": {
                "called": False,
                "execution_time_ms": 0.0,
                "input_data": {},
                "output_data": {},
                "decision_taken": "",
                "errors": [],
                "rerun": False,
                "status": "not_called",
            },
            "mix": {
                "called": False,
                "execution_time_ms": 0.0,
                "input_data": {},
                "output_data": {},
                "decision_taken": "",
                "errors": [],
                "rerun": False,
                "status": "not_called",
            }
        }

    def _planner(self):
        if self._planner_agent is None:
            from engines.ai_core.agents.agents_meta import PlannerAgent

            self._planner_agent = PlannerAgent()
        return self._planner_agent

    def _planner_report(self) -> dict[str, Any]:
        return self._agent_report.setdefault("planner", {})

    def _record_planner(self, *, elapsed_ms: float = 0.0, input_data=None, output_data=None,
                        decision: str = "", error: str | None = None) -> None:
        rep = self._planner_report()
        rep["rerun"] = bool(rep.get("called"))
        rep["called"] = True
        rep["execution_time_ms"] = round(
            float(rep.get("execution_time_ms") or 0.0) + float(elapsed_ms or 0.0), 1
        )
        if input_data:
            rep["input_data"] = dict(input_data)
        if output_data:
            rep["output_data"] = dict(output_data)
        if decision:
            rep["decision_taken"] = str(decision)
        if error:
            errs = list(rep.get("errors") or [])
            errs.append(str(error))
            rep["errors"] = errs
            rep["status"] = "error"
        else:
            rep["status"] = "ok"

    def _mix_report(self) -> dict[str, Any]:
        return self._agent_report.setdefault("mix", {})

    def _record_mix(self, *, elapsed_ms: float = 0.0, input_data=None, output_data=None,
                    decision: str = "", error: str | None = None) -> None:
        rep = self._mix_report()
        rep["rerun"] = bool(rep.get("called"))
        rep["called"] = True
        rep["execution_time_ms"] = round(
            float(rep.get("execution_time_ms") or 0.0) + float(elapsed_ms or 0.0), 1
        )
        if input_data:
            rep["input_data"] = dict(input_data)
        if output_data:
            rep["output_data"] = dict(output_data)
        if decision:
            rep["decision_taken"] = str(decision)
        if error:
            errs = list(rep.get("errors") or [])
            errs.append(str(error))
            rep["errors"] = errs
            rep["status"] = "error"
        else:
            rep["status"] = "ok"

    # ── Analysis ─────────────────────────────────────────────────────────
    def analyze(
        self,
        *,
        source_segments: Sequence[str] | None,
        translated_segments: Sequence[str] | None = None,
        timing_map: Sequence[Any] | None = None,
        src_lang: str = "",
        tgt_lang: str = "",
        content_mode_hint: str | None = None,
    ) -> ProjectProfile:
        self._planner_inputs = {
            "source_segments": list(source_segments or []),
            "translated_segments": list(translated_segments or []),
            "timing_map": list(timing_map or []),
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "content_mode_hint": content_mode_hint,
        }
        t0 = time.perf_counter()
        try:
            profile_dict = self._planner().analyze_profile(
                source_segments=source_segments,
                timing_map=timing_map,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                content_mode_hint=content_mode_hint,
            )
            self.profile = ProjectProfile(**profile_dict)
            self._record_planner(
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                input_data={
                    "source_count": len(source_segments or []),
                    "translated_count": len(translated_segments or []),
                    "timing_count": len(timing_map or []),
                    "src_lang": src_lang,
                    "tgt_lang": tgt_lang,
                    "content_mode_hint": content_mode_hint,
                },
                output_data={"profile": self.profile.to_dict()},
                decision=f"profile:{self.profile.content_type}/{self.profile.tempo}",
            )
        except Exception as exc:
            self._record_planner(
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                error=f"analyze:{type(exc).__name__}",
            )
            raise
        logger.info(
            "[AICore] task=%s analyzed: type=%s genre=%s tempo=%s emotion=%s "
            "complexity=%s segments=%d",
            self.task_id, self.profile.content_type, self.profile.genre,
            self.profile.tempo, self.profile.dominant_emotion,
            self.profile.complexity, self.profile.segment_count,
        )
        return self.profile

    # ── Planning (decisions) ─────────────────────────────────────────────
    def plan(
        self,
        *,
        requested_mode: str | None = None,
        per_segment_budget_s: float | None = None,
        project_budget_s: float | None = None,
    ) -> ProjectStrategy:
        """Decide the project strategy and apply it to the executors.

        Applying means: install the adaptation profile override (variant counts)
        and configure the per-segment LLM budget/mode via the gateway.
        """
        profile = self.profile or ProjectProfile()
        t0 = time.perf_counter()
        try:
            strategy_dict = self._planner().build_plan(
                profile,
                requested_mode=requested_mode,
            )
            self.strategy = ProjectStrategy(**strategy_dict)
        except Exception as exc:
            self._record_planner(
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                error=f"plan:{type(exc).__name__}",
            )
            raise
        if per_segment_budget_s and per_segment_budget_s > 0:
            self.strategy.per_segment_budget_s = float(per_segment_budget_s)
        if project_budget_s and project_budget_s > 0:
            self.strategy.project_budget_s = float(project_budget_s)

        # Executors obey AI Core: configure budget/mode first (this may reset
        # per-run state), THEN install the variant profile override for this run.
        llm_gateway.begin_run(
            self.task_id,
            mode=self.strategy.speed_mode,
            per_segment_s=self.strategy.per_segment_budget_s or None,
            project_s=self.strategy.project_budget_s or None,
        )
        try:
            from engines.ai_adaptation_engine import set_adaptation_profile_override

            set_adaptation_profile_override(self.strategy.adaptation_profile_override())
        except Exception:
            logger.debug("[AICore] could not set adaptation profile override", exc_info=True)
        self._record_planner(
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            input_data={
                **(self._planner_report().get("input_data") or {}),
                "requested_mode": requested_mode,
                "per_segment_budget_s": self.strategy.per_segment_budget_s,
                "project_budget_s": self.strategy.project_budget_s,
            },
            output_data={
                "profile": self.profile.to_dict() if self.profile else {},
                "strategy": self.strategy.to_dict(),
            },
            decision=(
                f"mode:{self.strategy.speed_mode};"
                f"llm_policy:{self.strategy.llm_policy};"
                f"model:{self.strategy.model}"
            ),
        )
        logger.info("[AICore] task=%s strategy: %s", self.task_id, "; ".join(self.strategy.rationale))
        return self.strategy

    # ── Execution ────────────────────────────────────────────────────────
    def adapt_segments(
        self,
        segments: list[str],
        timing_map: Sequence[Any] | None,
        source_segments: list[str] | None,
        *,
        src_lang: str,
        tgt_lang: str,
        raw_mt_segments: list[str] | None = None,
        progress_cb=None,
    ):
        """Drive the per-segment adaptive dubbing pipeline under the strategy.

        AI Core 3.0: when the multi-agent coordinator is enabled (default), the
        segment work is driven by the ordered agent pipeline (coordinator). On
        ANY failure it falls back to the legacy single-engine timing-aware path,
        so this is always safe. Set ``VM_AI_AGENTS=0`` to force the legacy path.
        """
        if _agents_enabled():
            try:
                return self.adapt_segments_agents(
                    segments,
                    timing_map,
                    source_segments,
                    src_lang=src_lang,
                    tgt_lang=tgt_lang,
                    raw_mt_segments=raw_mt_segments,
                    progress_cb=progress_cb,
                )
            except Exception:
                logger.warning(
                    "[AICore] multi-agent coordinator failed; using legacy path",
                    exc_info=True,
                )
        return self._adapt_segments_legacy(
            segments,
            timing_map,
            source_segments,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            raw_mt_segments=raw_mt_segments,
            progress_cb=progress_cb,
        )

    def _adapt_segments_legacy(
        self,
        segments: list[str],
        timing_map: Sequence[Any] | None,
        source_segments: list[str] | None,
        *,
        src_lang: str,
        tgt_lang: str,
        raw_mt_segments: list[str] | None = None,
        progress_cb=None,
    ):
        """Legacy single-engine timing-aware path (pre-3.0 behaviour)."""
        from engines.timing_aware_translation import adapt_segments_to_timing

        strat = self.strategy or self.plan()
        return adapt_segments_to_timing(
            segments,
            timing_map,
            source_segments,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            task_id=self.task_id,
            raw_mt_segments=raw_mt_segments,
            speed_mode=strat.speed_mode,
            per_segment_budget_s=strat.per_segment_budget_s or None,
            project_budget_s=strat.project_budget_s or None,
            progress_cb=progress_cb,
        )

    def adapt_segments_agents(
        self,
        segments: list[str],
        timing_map: Sequence[Any] | None,
        source_segments: list[str] | None,
        *,
        src_lang: str,
        tgt_lang: str,
        raw_mt_segments: list[str] | None = None,
        progress_cb=None,
    ):
        """Drive the per-segment pipeline via the AI Core 3.0 multi-agent coordinator."""
        from engines.ai_core.agents import AgentCoordinator

        strat = self.strategy or self.plan()
        profile = self.profile.to_dict() if self.profile else {}
        coordinator = AgentCoordinator(self.task_id, profile, strat.to_dict())
        self._coordinator = coordinator
        return coordinator.run(
            segments,
            timing_map,
            source_segments,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            raw_mt_segments=raw_mt_segments,
            progress_cb=progress_cb,
        )

    def decide_voice(self, *, segment_emotion: str | None = None) -> VoiceDirection:
        return decide_voice(self.profile or ProjectProfile(), segment_emotion=segment_emotion)

    def decide_mix_plan(self) -> dict[str, Any]:
        """Run the existing Mix Agent once and return its project-level mix plan."""
        from engines.ai_core.agents.agents_meta import MixAgent

        strategy = self.strategy.to_dict() if self.strategy else ProjectStrategy().to_dict()
        t0 = time.perf_counter()
        try:
            plan = MixAgent().plan(strategy)
            self._record_mix(
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                input_data={"strategy": strategy},
                output_data={"mix_plan": plan},
                decision=(
                    f"preserve_music:{bool(plan.get('preserve_music'))};"
                    f"ducking:{bool(plan.get('ducking_enabled'))};"
                    f"output:{plan.get('output') or ''}"
                ),
            )
            return plan
        except Exception as exc:
            self._record_mix(
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                input_data={"strategy": strategy},
                error=f"mix:{type(exc).__name__}",
            )
            raise

    # ── The single LLM entry ─────────────────────────────────────────────
    def llm(self, prompt: str, **kwargs) -> str | None:
        """All LLM calls flow through here (audit / budget / cache)."""
        return llm_gateway.chat(prompt, **kwargs)

    # ── Reporting ────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "profile": self.profile.to_dict() if self.profile else {},
            "strategy": self.strategy.to_dict() if self.strategy else {},
            "llm_status": llm_gateway.status(),
            "agent_report": self._agent_report,
        }


# ── Per-task registry ─────────────────────────────────────────────────────
_CORES: dict[str, AICore] = {}
_CORES_LOCK = threading.Lock()
_CURRENT: dict[str, str] = {"task_id": ""}


def get_ai_core(task_id: str = "") -> AICore:
    """Return (creating if needed) the AI Core for a task and mark it current."""
    tid = str(task_id or "")
    with _CORES_LOCK:
        core = _CORES.get(tid)
        if core is None:
            core = AICore(tid)
            _CORES[tid] = core
        _CURRENT["task_id"] = tid
        return core


def current_ai_core() -> AICore | None:
    """Return the most recently used AI Core, if any (for module access)."""
    with _CORES_LOCK:
        return _CORES.get(_CURRENT.get("task_id", ""))


def release_ai_core(task_id: str) -> None:
    with _CORES_LOCK:
        _CORES.pop(str(task_id or ""), None)
