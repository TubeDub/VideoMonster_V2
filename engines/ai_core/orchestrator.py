"""AI Core Orchestrator — unified agent pipeline with timeout, fallback, OpenDDF."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.debug_helpers import finalize_agent_status, is_debug_learning_mode
from engines.ai_core.director_agent import DirectorAgent
from engines.ai_core.grammar_agent import GrammarAgent
from engines.ai_core.mix_agent import MixAgent
from engines.ai_core.planner_agent import PlannerAgent
from engines.ai_core.quality_agent import QualityAgent
from engines.ai_core.reviewer_agent import ReviewerAgent
from engines.ai_core.semantic_agent import SemanticAgent
from engines.ai_core.timing_agent import TimingAgent
from engines.ai_core.translation_agent import TranslationAgent
from engines.ai_core.translation_agent.agent import load_manifest
from engines.ai_core.voice_agent import VoiceAgent
from engines.ai_core.voice_preparation_agent import VoicePreparationAgent
from engines.ai_core.voice_verification_agent import VoiceVerificationAgent
from engines.ai_core.voice_quality_agent import VoiceQualityAgent  # backward compat alias
from engines.ai_core.streaming_pipeline import StreamingTextPipelineRunner
from engines.ai_core.pipeline_heartbeat import emit_ai_core_heartbeat, orchestrator_timeout_scale
from engines.ai_core.timeout_policy import log_agent_timeout_debug, resolve_agent_timeout
from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

logger = logging.getLogger("tubedub.ai_core.orchestrator")

_APP_DIR = Path(__file__).resolve().parents[2]
_OUTPUT_DIR = _APP_DIR / "output"

AgentHook = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class PipelineHooks:
    """External pipeline steps wired by auto_dub_api."""

    stt: AgentHook | None = None
    tts: AgentHook | None = None
    mix: Callable[[str], tuple[bool, str | None, list[str]]] | None = None


@dataclass
class PipelineResult:
    status: str  # completed | partial | critical_failure
    state: dict[str, Any]
    agent_results: dict[str, AgentExecutionResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    critical: bool = False
    execution_time_ms: float = 0.0

    @property
    def updated_state(self) -> dict[str, Any]:
        """Alias used by auto_dub_api integration."""
        return self.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "critical": self.critical,
            "execution_time_ms": self.execution_time_ms,
            "warnings": self.warnings,
            "errors": self.errors,
            "agents": {
                name: res.to_dict() for name, res in self.agent_results.items()
            },
        }


class AICoreOrchestrator:
    """Run AI Core agents end-to-end with per-agent timeout and fallbacks."""

    AGENT_CHAIN: list[tuple[str, type | None, int]] = [
        ("planner", PlannerAgent, 30),
        ("stt", None, 600),
        ("director", DirectorAgent, 60),
        ("translation", TranslationAgent, 120),
        ("semantic", SemanticAgent, 90),
        ("timing", TimingAgent, 90),
        ("grammar", GrammarAgent, 60),
        ("quality", QualityAgent, 120),
        ("reviewer", ReviewerAgent, 60),
        ("voice_preparation", VoicePreparationAgent, 30),
        ("voice", VoiceAgent, 300),
        ("voice_verification", VoiceVerificationAgent, 120),
        ("mix", MixAgent, 180),
    ]

    _AGENT_INSTANCES: dict[str, type] = {
        "planner": PlannerAgent,
        "director": DirectorAgent,
        "translation": TranslationAgent,
        "semantic": SemanticAgent,
        "timing": TimingAgent,
        "grammar": GrammarAgent,
        "quality": QualityAgent,
        "reviewer": ReviewerAgent,
        "voice_preparation": VoicePreparationAgent,
        "voice": VoiceAgent,
        "voice_verification": VoiceVerificationAgent,
        "voice_quality": VoiceQualityAgent,
        "mix": MixAgent,
        "streaming_text": StreamingTextPipelineRunner,
    }

    def __init__(self, hooks: PipelineHooks | None = None, output_dir: Path | None = None):
        self.hooks = hooks or PipelineHooks()
        self.output_dir = output_dir or _OUTPUT_DIR

    def _filter_chain(
        self,
        chain: list[tuple[str, type | None, int]],
        state: dict[str, Any],
        agents: list[str] | None,
        skip: set[str],
    ) -> list[tuple[str, type | None, int]]:
        if agents:
            allowed = set(agents)
            chain = [c for c in chain if c[0] in allowed]

        start_after = str(state.get("start_after") or "").strip()
        stop_before = str(state.get("stop_before") or "").strip()
        if start_after:
            names = [c[0] for c in chain]
            if start_after in names:
                chain = chain[names.index(start_after) + 1 :]
        if stop_before:
            names = [c[0] for c in chain]
            if stop_before in names:
                chain = chain[: names.index(stop_before)]

        return [c for c in chain if c[0] not in skip]

    def _apply_streaming_mode(
        self,
        chain: list[tuple[str, type | None, int]],
        state: dict[str, Any],
    ) -> list[tuple[str, type | None, int]]:
        """Replace text-agent block with single streaming_text mode (AI Core 4.2)."""
        try:
            from engines.ai_core.platform.feature_registry import is_platform_feature_enabled

            if not is_platform_feature_enabled("streaming_pipeline"):
                return chain
        except Exception:
            pass

        from engines.ai_core.streaming_pipeline.mode import (
            PIPELINE_MODE_STREAMING,
            resolve_pipeline_mode,
        )

        if resolve_pipeline_mode(state) != PIPELINE_MODE_STREAMING:
            return chain

        names = [c[0] for c in chain]
        block_candidates = [
            "translation",
            "semantic",
            "timing",
            "grammar",
            "quality",
            "reviewer",
            "voice_preparation",
        ]
        if state.get("segment_tts_handler") or state.get("streaming_voice"):
            block_candidates.append("voice")
        block = [s for s in block_candidates if s in names]
        if len(block) < 2:
            return chain

        first_i = names.index(block[0])
        last_i = names.index(block[-1])
        timeout = max(sum(c[2] for c in chain if c[0] in block), 300)
        try:
            from engines.translation_adapt import _is_cpu_only

            if _is_cpu_only():
                timeout = max(timeout, 600)
        except Exception:
            pass
        state["_streaming_chain_names"] = names
        state["streaming_stages"] = tuple(block)
        state["pipeline_mode"] = PIPELINE_MODE_STREAMING

        return (
            chain[:first_i]
            + [("streaming_text", StreamingTextPipelineRunner, timeout)]
            + chain[last_i + 1 :]
        )

    def run_pipeline(
        self,
        task_id: str,
        video_path: str,
        manifest_path: str,
        state: dict[str, Any],
        *,
        agents: list[str] | None = None,
        skip_agents: set[str] | None = None,
    ) -> PipelineResult:
        """Run agents with timeout, fallback, OpenDDF recording.

        NEVER stop except critical failures: missing video, no audio track,
        or unwritable output directory.
        """
        t0 = time.perf_counter()
        warnings: list[str] = []
        errors: list[str] = []
        agent_results: dict[str, AgentExecutionResult] = {}
        working_state = dict(state)
        skip = set(skip_agents or ())

        chain = self._filter_chain(self.AGENT_CHAIN, working_state, agents, skip)
        chain = self._apply_streaming_mode(chain, working_state)
        scale = orchestrator_timeout_scale()
        if scale != 1.0:
            chain = [(n, c, max(int(t * scale), t)) for n, c, t in chain]

        crit_err = self._critical_preflight(video_path, task_id, chain)
        if crit_err:
            return PipelineResult(
                status="critical_failure",
                state=working_state,
                agent_results=agent_results,
                warnings=warnings,
                errors=[crit_err],
                critical=True,
                execution_time_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

        manifest: dict[str, Any] = {}
        if manifest_path and Path(manifest_path).is_file():
            try:
                manifest = load_manifest(manifest_path)
            except Exception as exc:
                warnings.append(f"manifest_load_failed:{exc}")
                if not IS_DEBUG_LEARNING_MODE():
                    errors.append(str(exc))

        # Skip agents already completed (idempotent re-entry).
        _done_flags = {
            "director": "director_agent_path",
            "semantic": "semantic_agent_path",
            "timing": "timing_agent_path",
            "grammar": "grammar_agent_path",
            "quality": "quality_agent_path",
            "reviewer": "reviewer_agent_path",
            "translation": "translation_agent_path",
            "voice_verification": "voice_verification_agent_path",
            "voice_quality": "voice_verification_agent_path",
        }
        chain = [
            c for c in chain
            if not (c[0] in _done_flags and working_state.get(_done_flags[c[0]]))
        ]

        try:
            from engines.ai_core import llm_gateway

            llm_gateway.begin_run(task_id)
        except Exception:
            pass

        from engines.ai_core.global_skill import skill_version, to_dict as skill_to_dict
        from engines.ai_core.ai_network import (
            EVENT_AGENT_FINISHED,
            EVENT_AGENT_STARTED,
            EVENT_PIPELINE_FINISHED,
            EVENT_PIPELINE_STARTED,
            get_network,
            reset_network,
            save_network_journal,
        )
        from engines.ai_core.ai_event_log import agent_finished, agent_started
        from engines.ai_core.unified_diagnostics import save_unified_diagnostics

        from engines.ai_core.development_lifecycle import advance_pipeline_lifecycle, record_stage, STAGE_PLANNING
        from engines.ai_core.platform import get_bus, platform_versions, reset_bus, save_bus_snapshot
        from engines.ai_core.services.ai_memory import get_memory_service, reset_memory_service
        from engines.ai_core.observability import get_observability, record_agent_execution, reset_observability
        from engines.ai_core.ai_network.dag import dag_snapshot

        reset_network(task_id)
        reset_bus(task_id)
        reset_memory_service(task_id)
        reset_observability(task_id)
        net = get_network(task_id)
        bus = get_bus(task_id)
        memory = get_memory_service(task_id)
        observability = get_observability(task_id)
        working_state["global_skill_version"] = skill_version()
        working_state["global_skill"] = skill_to_dict()
        working_state["platform_versions"] = platform_versions()
        working_state["ai_network_dag"] = dag_snapshot(working_state)
        record_stage(task_id, STAGE_PLANNING, detail="pipeline_start", app_dir=_APP_DIR)

        active_model = {}
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            caps = detect_capabilities()
            active_model = {
                "model": caps.get("model"),
                "provider": caps.get("provider"),
            }
        except Exception:
            pass

        net.publish(
            EVENT_PIPELINE_STARTED,
            "orchestrator",
            {
                "task_id": task_id,
                "pipeline_mode": working_state.get("pipeline_mode"),
                "agent_chain": [c[0] for c in chain],
                "global_skill_version": working_state["global_skill_version"],
                "active_model": active_model,
            },
        )
        agent_started(task_id, "Pipeline", model=active_model.get("model") or "")

        from engines.ai_core.architecture_validation import (
            ArchitectureMetrics,
            write_architecture_validation,
        )

        arch_metrics = ArchitectureMetrics(task_id=task_id)
        working_state["architecture_metrics"] = arch_metrics

        for name, agent_cls, timeout_sec in chain:
            if name in skip:
                continue
            if name == "planner" and manifest.get("project_uuid"):
                continue
            timeout_sec = resolve_agent_timeout(name, timeout_sec, working_state)
            emit_ai_core_heartbeat(
                task_id,
                agent=name,
                live_message=f"AI Core: {name}",
                ai_core_agent_timeout_sec=timeout_sec,
            )
            agent_started(task_id, name, model=active_model.get("model") or "")
            net.publish(
                EVENT_AGENT_STARTED,
                "orchestrator",
                {"agent": name, "timeout_sec": timeout_sec},
            )
            if name == "stt":
                result = self._run_hook_agent(
                    task_id, name, self.hooks.stt, working_state, timeout_sec
                )
                if result:
                    agent_results[name] = result
                    working_state.update(result.updated_state)
                    agent_finished(
                        task_id,
                        name,
                        status=result.status,
                        ms=float(result.execution_time_ms or 0),
                        model=active_model.get("model") or "",
                    )
                    net.publish(
                        EVENT_AGENT_FINISHED,
                        "orchestrator",
                        {
                            "agent": name,
                            "status": result.status,
                            "ms": float(result.execution_time_ms or 0),
                        },
                    )
                continue

            if name == "voice":
                agent = VoiceAgent(tts_hook=self.hooks.tts)
                result = self._run_agent_bounded(
                    task_id, name, agent, manifest, working_state, timeout_sec
                )
            elif name == "mix":
                agent = MixAgent(mix_hook=self.hooks.mix)
                result = self._run_agent_bounded(
                    task_id, name, agent, manifest, working_state, timeout_sec
                )
            elif agent_cls is not None:
                self._inject_upstream_status(name, working_state, agent_results)
                peer_ok = True
                peer_return_count = 0
                if name in (
                    "semantic", "timing", "grammar", "quality", "reviewer",
                    "voice_preparation", "voice", "voice_verification", "mix",
                ) and name != "streaming_text":
                    try:
                        from engines.ai_core.peer_validation_loop import run_peer_validation_gate

                        peer_ok, peer_warn, peer_log = run_peer_validation_gate(
                            name,
                            manifest,
                            working_state,
                            task_id,
                            app_dir=self.output_dir.parent
                            if self.output_dir.name == "output"
                            else _APP_DIR,
                        )
                        warnings.extend(peer_warn)
                        peer_return_count = sum(
                            1 for e in peer_log if e.get("action") == "return_to_upstream"
                        )
                        for entry in peer_log:
                            if entry.get("action") in (
                                "return_to_upstream",
                                "unresolved_at_gate",
                                "max_returns_exceeded",
                            ):
                                arch_metrics.record_peer_return(entry)
                        working_state["peer_validation_log_path"] = str(
                            _APP_DIR / "output" / "diagnostics" / task_id / "peer_validation_log.json"
                        )
                        if not peer_ok:
                            warnings.append(f"{name}:peer_validation_gate_failed")
                            arch_metrics.record_agent(
                                name,
                                status="blocked",
                                peer_ok=False,
                                peer_returns=peer_return_count,
                            )
                            if not IS_DEBUG_LEARNING_MODE():
                                blocked = AgentExecutionResult(
                                    status="warning",
                                    updated_state=working_state,
                                    metrics={"peer_validation_blocked": True},
                                    warnings=peer_warn + [f"{name}:peer_validation_blocked"],
                                    errors=[],
                                    execution_time_ms=0.0,
                                    decision_log=["peer_validation_blocked"],
                                )
                                agent_results[name] = blocked
                                agent_finished(task_id, name, status="blocked", ms=0.0)
                                net.publish(
                                    EVENT_AGENT_FINISHED,
                                    "orchestrator",
                                    {"agent": name, "status": "blocked", "ms": 0},
                                )
                                continue
                    except Exception as exc:
                        warnings.append(f"{name}:peer_validation_error:{exc}")

                before_segments = copy.deepcopy(working_state.get("segments") or [])
                agent = agent_cls()
                result = self._run_agent_bounded(
                    task_id, name, agent, manifest, working_state, timeout_sec
                )
                after_segments = list((result.updated_state or {}).get("segments") or before_segments)
                try:
                    from engines.ai_core.contract_enforcement import validate_all_segment_writes

                    violations = validate_all_segment_writes(name, before_segments, after_segments)
                    if violations:
                        arch_metrics.contract_violations.extend(
                            [f"{name}:{v}" for v in violations[:20]]
                        )
                        warnings.extend(violations[:5])
                except Exception:
                    pass

                arch_metrics.record_agent(
                    name,
                    execution_time_ms=float(result.execution_time_ms or 0),
                    status=result.status,
                    peer_ok=peer_ok,
                    peer_returns=peer_return_count,
                )
            else:
                continue

            agent_results[name] = result
            working_state.update(result.updated_state)
            if name == "planner":
                mp = working_state.get("manifest_path")
                if mp and Path(mp).is_file():
                    try:
                        manifest = load_manifest(mp)
                        bus.publish_manifest(manifest, source="planner")
                    except Exception as exc:
                        warnings.append(f"manifest_bus_publish_failed:{exc}")
            elif manifest:
                bus.publish_manifest(manifest, source="orchestrator")
            try:
                bus.update_state(
                    name,
                    {
                        k: v
                        for k, v in (result.updated_state or {}).items()
                        if k.endswith("_path") or k in ("project_uuid", "pipeline_mode", "segments")
                    },
                )
            except Exception:
                pass
            _agent_status = result.status if result else "skipped"
            _agent_ms = float(getattr(result, "execution_time_ms", 0) or 0)
            agent_finished(
                task_id,
                name,
                status=_agent_status,
                ms=_agent_ms,
                model=active_model.get("model") or "",
            )
            net.publish(
                EVENT_AGENT_FINISHED,
                "orchestrator",
                {
                    "agent": name,
                    "status": _agent_status,
                    "ms": _agent_ms,
                    "warnings": len(result.warnings or []) if result else 0,
                },
            )
            try:
                from engines.ai_core.platform.feature_registry import is_platform_feature_enabled

                if is_platform_feature_enabled("ai_memory"):
                    memory.record_agent_run(
                        name,
                        list(working_state.get("segments") or []),
                        status=_agent_status,
                        ms=_agent_ms,
                    )
                record_agent_execution(
                    task_id,
                    name,
                    status=_agent_status,
                    ms=_agent_ms,
                    segments=list(working_state.get("segments") or []),
                    model=active_model.get("model") or "",
                )
            except Exception:
                pass
            if name == "reviewer" and result:
                from engines.ai_core.reviewer_gate import review_agent_output

                skill_check = review_agent_output(
                    task_id,
                    "reviewer",
                    status=result.status,
                    segments=list(working_state.get("segments") or []),
                    tgt_lang=str(working_state.get("target_lang") or manifest.get("target_lang") or ""),
                    errors=list(result.errors or []),
                )
                working_state["global_skill_check"] = skill_check
            elif result and name in (
                "translation", "semantic", "timing", "grammar", "quality",
            ):
                from engines.ai_core.reviewer_gate import review_agent_output

                review_agent_output(
                    task_id,
                    name,
                    status=result.status,
                    segments=list(working_state.get("segments") or []),
                    tgt_lang=str(working_state.get("target_lang") or manifest.get("target_lang") or ""),
                    errors=list(result.errors or []),
                )
            if name == "streaming_text" and result.status in ("success", "warning"):
                for sub in (
                    "translation",
                    "semantic",
                    "timing",
                    "grammar",
                    "quality",
                    "reviewer",
                    "voice_preparation",
                    "voice",
                ):
                    if sub in (working_state.get("streaming_stages") or ()):
                        working_state[f"{sub}_agent_path"] = True
                        working_state[f"{sub}_agent_status"] = result.status
                if "voice" in (working_state.get("streaming_stages") or ()):
                    working_state["streaming_voice_done"] = True
            if name in _done_flags and result.status in ("success", "warning"):
                working_state[_done_flags[name]] = True
                working_state[f"{name}_agent_status"] = result.status
                if result.updated_state.get(f"{name}_report_path"):
                    working_state[f"{name}_report_path"] = result.updated_state[
                        f"{name}_report_path"
                    ]
            warnings.extend(result.warnings)
            if result.status == "error" and not IS_DEBUG_LEARNING_MODE():
                errors.extend(result.errors)

        status = "completed"
        if errors and not IS_DEBUG_LEARNING_MODE():
            status = "partial"
        if IS_DEBUG_LEARNING_MODE() and (warnings or any(r.status == "warning" for r in agent_results.values())):
            status = "completed"

        arch_metrics.pipeline_status = status
        try:
            av_path = write_architecture_validation(task_id, arch_metrics, app_dir=_APP_DIR)
            working_state["architecture_validation_path"] = str(av_path)
        except Exception as exc:
            logger.debug("architecture_validation save skipped: %s", exc)

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        agent_finished(task_id, "Pipeline", status=status, ms=elapsed)
        net.publish(
            EVENT_PIPELINE_FINISHED,
            "orchestrator",
            {"status": status, "execution_time_ms": elapsed, "agents": list(agent_results.keys())},
        )
        try:
            advance_pipeline_lifecycle(task_id, phase="pipeline_done", app_dir=_APP_DIR)
            save_network_journal(task_id, app_dir=_APP_DIR)
            save_bus_snapshot(task_id, app_dir=_APP_DIR)
            try:
                memory.save(app_dir=_APP_DIR)
                observability.save(app_dir=_APP_DIR)
            except Exception:
                pass
            ud_path = save_unified_diagnostics(
                task_id,
                app_dir=_APP_DIR,
                task_info={
                    **(working_state.get("task_info") or {}),
                    **working_state,
                    "whisper_model": working_state.get("whisper_model")
                    or (working_state.get("task_info") or {}).get("whisper_model"),
                    "tts_engine": working_state.get("tts_engine") or "edge-tts",
                    "pipeline_mode": working_state.get("pipeline_mode"),
                    "global_skill_version": working_state.get("global_skill_version"),
                },
            )
            working_state["unified_diagnostics_path"] = str(ud_path)
        except Exception as exc:
            logger.debug("unified diagnostics save skipped: %s", exc)
        try:
            from engines.open_ddf import open_ddf

            open_ddf.save(task_id)
        except Exception:
            pass

        try:
            from engines.ai_core.report import save_ai_core_report

            save_ai_core_report(task_id, task_info=working_state.get("task_info"))
        except Exception as exc:
            logger.debug("ai_core report save skipped: %s", exc)

        return PipelineResult(
            status=status,
            state=working_state,
            agent_results=agent_results,
            warnings=warnings,
            errors=errors,
            critical=False,
            execution_time_ms=elapsed,
        )

    def _critical_preflight(
        self,
        video_path: str,
        task_id: str,
        chain: list[tuple[str, type | None, int]],
    ) -> str | None:
        needs_video = any(name in ("planner", "stt") for name, _, _ in chain)
        path = Path(str(video_path or ""))
        if needs_video and video_path and not path.is_file():
            return f"video_not_found:{video_path}"
        try:
            _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            probe = _OUTPUT_DIR / f".write_probe_{task_id[:8]}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            return f"output_dir_not_writable:{exc}"
        return None

    @staticmethod
    def _inject_upstream_status(
        name: str,
        state: dict[str, Any],
        results: dict[str, AgentExecutionResult],
    ) -> None:
        """Set agent dependency status fields expected by gatekeeper."""
        upstream = {
            "translation": ("director", "director_agent_status"),
            "semantic": ("translation", "translation_agent_status"),
            "timing": ("semantic", "semantic_agent_status"),
            "grammar": ("timing", "timing_agent_status"),
            "quality": ("grammar", "grammar_agent_status"),
            "reviewer": ("quality", "quality_agent_status"),
            "voice_preparation": ("reviewer", "reviewer_agent_status"),
            "voice_verification": ("voice", "voice_agent_status"),
            "voice_quality": ("voice", "voice_agent_status"),
            "mix": ("voice_verification", "voice_verification_agent_status"),
        }
        if name not in upstream:
            return
        up_name, field = upstream[name]
        prev = results.get(up_name)
        if prev:
            state[field] = prev.status
        elif not state.get(field):
            state[field] = "success"

    def _run_hook_agent(
        self,
        task_id: str,
        name: str,
        hook: AgentHook | None,
        state: dict[str, Any],
        timeout_sec: int,
    ) -> AgentExecutionResult | None:
        if hook is None:
            return None
        t0 = time.perf_counter()

        def _invoke() -> dict[str, Any]:
            return hook(state) or {}

        ok, value, err = self._run_with_timeout(_invoke, timeout_sec)
        elapsed = (time.perf_counter() - t0) * 1000
        if not ok:
            self._record_ddf(
                task_id, name, success=False, error=err or "timeout",
                fallback_used=True, fallback_reason=err or "timeout",
                execution_time_ms=elapsed,
            )
            return AgentExecutionResult(
                status=finalize_agent_status("warning"),
                updated_state=state,
                metrics={},
                warnings=[err or "timeout"],
                errors=[],
                execution_time_ms=round(elapsed, 1),
                decision_log=["hook_timeout_fallback"],
            )
        self._record_ddf(
            task_id, name, success=True, execution_time_ms=elapsed,
            output_metrics={"keys": list(value.keys())},
        )
        return AgentExecutionResult(
            status="success",
            updated_state={**state, **value},
            metrics={},
            warnings=[],
            errors=[],
            execution_time_ms=round(elapsed, 1),
            decision_log=["hook_ok"],
        )

    def _run_agent_bounded(
        self,
        task_id: str,
        name: str,
        agent: Any,
        manifest: dict[str, Any],
        state: dict[str, Any],
        timeout_sec: int,
    ) -> AgentExecutionResult:
        t0 = time.perf_counter()

        def _invoke() -> AgentExecutionResult:
            if name == "planner":
                return agent.run(
                    state.get("video_path", ""),
                    state.get("target_lang", manifest.get("target_lang", "ru")),
                    state.get("source_lang", manifest.get("source_lang", "")),
                    task_id=task_id,
                )
            return agent.run(manifest, state, task_id)

        ok, value, err = self._run_with_timeout(_invoke, timeout_sec)
        elapsed = (time.perf_counter() - t0) * 1000

        _TEXT_AGENTS = frozenset(
            {
                "translation",
                "semantic",
                "timing",
                "grammar",
                "quality",
                "streaming_text",
            }
        )
        max_retries = max(0, int(os.getenv("VM_AI_CORE_MAX_RETRIES", "2")))
        if (
            not ok
            and name in _TEXT_AGENTS
            and err
            and "timeout" not in str(err).lower()
            and max_retries > 0
        ):
            from engines.pipeline_orchestrator.stage_retry import run_with_retry

            def _on_retry(attempt: int, msg: str, delay: float) -> None:
                emit_ai_core_heartbeat(
                    task_id,
                    agent=name,
                    substep="retry",
                    live_message=(
                        f"AI Core: {name} — повтор {attempt}/{max_retries} "
                        f"({msg[:80]}), пауза {delay:.0f}с"
                    ),
                    ai_core_retry=True,
                    ai_core_retry_attempt=attempt,
                )

            def _attempt() -> AgentExecutionResult:
                a_ok, a_val, a_err = self._run_with_timeout(_invoke, timeout_sec)
                if not a_ok:
                    raise RuntimeError(str(a_err or "agent_failed"))
                if not isinstance(a_val, AgentExecutionResult):
                    raise RuntimeError("invalid_agent_result")
                return a_val

            retry = run_with_retry(
                _attempt,
                max_attempts=max_retries + 1,
                stage=f"ai_core_{name}",
                on_retry=_on_retry,
            )
            if retry.ok and isinstance(retry.value, AgentExecutionResult):
                value = retry.value
                ok = True
                err = None
                emit_ai_core_heartbeat(
                    task_id,
                    agent=name,
                    substep="retry_ok",
                    live_message=f"AI Core: {name} — успешно после {retry.attempts} попыток",
                )

        if (
            not ok
            and name in _TEXT_AGENTS
            and err
            and "timeout" in str(err).lower()
        ):
            debug_payload = log_agent_timeout_debug(
                self.output_dir.parent
                if self.output_dir.name == "output"
                else _APP_DIR,
                task_id,
                agent=name,
                timeout_sec=timeout_sec,
                error=str(err),
                state=state,
            )
            emit_ai_core_heartbeat(
                task_id,
                agent=name,
                substep="timeout_fallback",
                live_message=(
                    f"AI Core: {name} — таймаут {timeout_sec}с "
                    f"(LLM ожидание {debug_payload.get('wait_sec', '?')}с), fallback"
                ),
                ai_core_timeout=True,
                ai_core_timeout_sec=timeout_sec,
                llm_timeout_debug=debug_payload,
            )
            # Do not retry: _run_with_timeout leaves the worker thread running;
            # a second invoke would duplicate work and stall the UI for minutes.

        if ok and isinstance(value, AgentExecutionResult):
            self._record_ddf(
                task_id,
                name,
                success=value.status != "error",
                error=value.errors[0] if value.errors else None,
                fallback_used=value.status == "warning",
                execution_time_ms=value.execution_time_ms or elapsed,
                llm_calls=int((value.metrics or {}).get("llm_calls") or 0),
                input_metrics={"segments": len(state.get("segments") or [])},
                output_metrics=value.metrics,
            )
            return value

        fallback_state = self._rule_fallback_state(name, state)
        self._record_ddf(
            task_id,
            name,
            success=False,
            error=err or "timeout",
            fallback_used=True,
            fallback_reason=err or "agent_timeout",
            execution_time_ms=elapsed,
            decision="LLM skipped" if "llm" in str(err or "").lower() else "rule_fallback",
        )
        return AgentExecutionResult(
            status=finalize_agent_status("warning"),
            updated_state=fallback_state,
            metrics={"fallback": True},
            warnings=[err or f"{name}_timeout"],
            errors=[],
            execution_time_ms=round(elapsed, 1),
            decision_log=["timeout_rule_fallback"],
        )

    def _rule_fallback_state(self, agent_name: str, state: dict[str, Any]) -> dict[str, Any]:
        """Minimal rule-based fallback — keep last good text fields."""
        out = dict(state)
        segments = []
        for seg in list(state.get("segments") or []):
            row = dict(seg)
            text = str(
                row.get("grammar_text")
                or row.get("timing_text")
                or row.get("semantic_text")
                or row.get("translated_text")
                or row.get("text")
                or ""
            ).strip()
            if agent_name in ("translation", "semantic", "timing", "grammar"):
                if agent_name == "translation" and not row.get("translated_text"):
                    row["translated_text"] = str(row.get("text") or text)
                elif agent_name == "semantic" and not row.get("semantic_text"):
                    row["semantic_text"] = text
                elif agent_name == "timing" and not row.get("timing_text"):
                    row["timing_text"] = text
                elif agent_name == "grammar" and not row.get("grammar_text"):
                    row["grammar_text"] = text
            segments.append(row)
        if segments:
            out["segments"] = segments
        out.setdefault("fallback_agents", []).append(agent_name)
        return out

    @staticmethod
    def _run_with_timeout(
        fn: Callable[[], Any],
        timeout_sec: int,
    ) -> tuple[bool, Any, str | None]:
        if timeout_sec <= 0:
            try:
                return True, fn(), None
            except Exception as exc:
                return False, None, str(exc)

        box: dict[str, Any] = {"value": None, "error": None}

        def _target() -> None:
            try:
                box["value"] = fn()
            except Exception as exc:
                box["error"] = exc

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=timeout_sec)
        if thread.is_alive():
            return False, None, f"timeout_{timeout_sec}s"
        if box["error"] is not None:
            return False, None, str(box["error"])
        return True, box["value"], None

    @staticmethod
    def _record_ddf(task_id: str, agent_name: str, **kwargs: Any) -> None:
        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(task_id, agent_name, **kwargs)
        except Exception:
            pass


__all__ = [
    "AICoreOrchestrator",
    "PipelineHooks",
    "PipelineResult",
]
