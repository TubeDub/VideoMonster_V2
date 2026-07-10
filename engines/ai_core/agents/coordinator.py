"""AI Core 3.0 — Agent Coordinator.

AI Core is now a *coordinator only*. This module owns the ordered per-segment
agent pipeline and the cross-segment scheduling, but does none of the agents'
actual work — each agent is a single-responsibility unit that reuses the
existing, hardened engines.

Per segment the coordinator runs::

    Translation → Semantic → Entity → Timing → Grammar → Quality → Voice

with two bounded feedback rules:

* Fidelity repair — if the Semantic or Entity agent reports a loss it routes the
  segment back to the Translation Agent (bounded rounds), then continues forward.
* Quality gate — the Quality Agent returns work to ONLY the single responsible
  agent (not the whole chain), bounded rounds; if it still cannot pass, the
  coordinator emits a SAFE non-empty, non-English, non-truncated fallback.

Cross-segment it mirrors the existing timing-aware loop: each segment runs under
the wall-clock :func:`run_segment_bounded` watchdog and segments are processed in
parallel while real LLM calls stay serialized by the gateway semaphore. The
output is fully compatible with the legacy path — a list of adapted strings plus
a list of :class:`TimingAwareRecord` — with a per-segment ``agent_timeline``
stamped into each record's ``ai_adaptation_trace``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Sequence

from engines.ai_core.agents.agents_meta import MixAgent, PlannerAgent, VoiceAgent
from engines.ai_core.agents.agents_text import (
    EntityAgent,
    GrammarAgent,
    QualityAgent,
    SemanticAgent,
    TimingAgent,
    TranslationAgent,
)
from engines.ai_core.agents.base import AgentResult, SegmentContext

logger = logging.getLogger("tubedub.ai_core.agents")

# The per-segment forward chain (Quality + Voice handled separately below).
_FORWARD = ["translation", "semantic", "entity", "timing", "grammar"]

# Full agent order shown in the OpenDDF timeline (Planner + Mix are project-level).
TIMELINE_ORDER = [
    "planner", "translation", "semantic", "entity", "timing",
    "grammar", "quality", "voice", "mix",
]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


class AgentCoordinator:
    """Drives the ordered agent pipeline for one dub project."""

    def __init__(self, task_id: str, profile: dict[str, Any], strategy: dict[str, Any]):
        self.task_id = str(task_id or "")
        self.profile = dict(profile or {})
        self.strategy = dict(strategy or {})
        self.planner = PlannerAgent()
        self.mix = MixAgent()
        self.voice = VoiceAgent()
        self.agents = {
            "translation": TranslationAgent(),
            "semantic": SemanticAgent(),
            "entity": EntityAgent(),
            "timing": TimingAgent(),
            "grammar": GrammarAgent(),
        }
        self.quality = QualityAgent()
        self.max_fix_rounds = _env_int("VM_AI_AGENT_MAX_FIX_ROUNDS", 3)
        self.max_quality_rounds = _env_int("VM_AI_AGENT_MAX_QUALITY_ROUNDS", 2)

    # ── one segment ──────────────────────────────────────────────────────
    def _run_step(self, name: str, ctx: SegmentContext, timeline: list[dict[str, Any]],
                  *, force: bool = False) -> AgentResult:
        from engines.ai_core.ai_network.bridge import emit_agent_finished, emit_agent_started
        from engines.ai_core.reviewer_gate import review_agent_output

        emit_agent_started(self.task_id, name, segment_index=ctx.index)
        agent = self.agents[name]
        if not force and not agent.needed(ctx):
            res = AgentResult(agent=name, text=ctx.text, changed=False, skipped=True,
                              reason="not_needed")
        else:
            res = agent.run(ctx)
        timeline.append(res.to_timeline_row())
        emit_agent_finished(
            self.task_id,
            name,
            status="success" if res.ok else "warning",
            ms=res.elapsed_ms,
            segment_index=ctx.index,
        )
        review_agent_output(
            self.task_id,
            name,
            status="success" if res.ok else "warning",
            segments=[{"index": ctx.index, "text": ctx.text}],
            tgt_lang=ctx.tgt_lang,
        )
        return res

    def process_segment(self, ctx: SegmentContext) -> tuple[str, Any, list[dict[str, Any]]]:
        timeline: list[dict[str, Any]] = []

        # 1) Forward chain with bounded fidelity repair (Semantic/Entity → Translation).
        rounds = 0
        i = 0
        while i < len(_FORWARD):
            res = self._run_step(_FORWARD[i], ctx, timeline)
            if (not res.ok and res.route_back_to in _FORWARD
                    and rounds < self.max_fix_rounds):
                rounds += 1
                from engines.ai_core.ai_network.bridge import emit_recovery_action

                emit_recovery_action(
                    self.task_id,
                    from_agent=name,
                    to_agent=res.route_back_to,
                    segment_index=ctx.index,
                    reason=res.reason or "fidelity_repair",
                )
                i = _FORWARD.index(res.route_back_to)
                continue
            i += 1

        # 2) Quality gate — return work to ONLY the responsible agent, bounded.
        q_rounds = 0
        qres = self.quality.run(ctx)
        timeline.append(qres.to_timeline_row())
        while (not qres.ok and qres.route_back_to
               and q_rounds < self.max_quality_rounds):
            q_rounds += 1
            from engines.ai_core.ai_network.bridge import emit_recovery_action

            emit_recovery_action(
                self.task_id,
                from_agent="quality",
                to_agent=qres.route_back_to,
                segment_index=ctx.index,
                reason=qres.reason or "quality_gate",
            )
            self._run_step(qres.route_back_to, ctx, timeline, force=True)
            qres = self.quality.run(ctx)
            timeline.append(qres.to_timeline_row())

        # 3) Safety net: never emit empty / truncated / source-language text.
        final_text = self._safe_text(ctx)

        # 4) Voice delivery (per segment, no LLM).
        vres = self.voice.run(ctx)
        timeline.append(vres.to_timeline_row())

        record = self._build_record(ctx, final_text, qres, timeline)
        return final_text, record, timeline

    def _safe_text(self, ctx: SegmentContext) -> str:
        from engines.sentence_integrity import enforce_tts_integrity, validate_tts_text

        fallbacks: list[str] = []
        current = str(ctx.text or "").strip()
        raw = str(ctx.raw_translation or "").strip()
        if raw:
            fallbacks.append(raw)
        try:
            from engines.ai_core.agents.agents_text import GrammarAgent

            polished_raw = GrammarAgent._rule_polish(raw, ctx.tgt_lang).strip()
            if polished_raw and polished_raw not in fallbacks:
                fallbacks.append(polished_raw)
        except Exception:
            pass

        decision = enforce_tts_integrity(current, fallbacks=fallbacks, source="")
        chosen = str(decision.get("text") or "").strip()
        try:
            from engines.pipeline_language_gate import is_critical_language_mismatch

            bad, _ = is_critical_language_mismatch(
                chosen, target_lang=ctx.tgt_lang, original=ctx.source_text
            )
            if bad:
                chosen = ""
        except Exception:
            pass
        if chosen:
            ok, _issues = validate_tts_text(chosen)
            if ok:
                ctx.text = chosen
                return chosen

        # Last resort after a failed Quality gate: emit a complete target-language
        # safety line rather than leaking a broken / truncated candidate.
        lang = str(ctx.tgt_lang or "").split("-")[0].lower()
        placeholder = {
            "uk": "Репліка завершена.",
            "ru": "Реплика завершена.",
            "be": "Рэпліка завершана.",
        }.get(lang, "Реплика завершена.")
        ctx.text = placeholder
        return placeholder

    def _build_record(self, ctx: SegmentContext, final_text: str, qres: AgentResult,
                      timeline: list[dict[str, Any]]):
        from engines.semantic_adaptation import estimate_tts_duration_ms
        from engines.timing_aware_translation import TimingAwareRecord, word_count

        rec = ctx.timing_record
        if rec is None:
            rec = TimingAwareRecord(
                index=ctx.index,
                source_words=word_count(ctx.source_text),
                input_words=word_count(ctx.raw_translation or final_text),
                slot_ms=int(ctx.slot_ms or 0),
                text_before=ctx.raw_translation or final_text,
            )
        # Grammar / quality repairs may have changed the text after Timing ran.
        rec.text_after = final_text
        rec.output_words = word_count(final_text)
        try:
            rec.predicted_ms_after = estimate_tts_duration_ms(final_text, ctx.tgt_lang)
        except Exception:
            pass
        rec.adapted = bool(final_text.strip() != str(rec.text_before or "").strip())

        trace = dict(rec.ai_adaptation_trace or {})
        trace["agent_timeline"] = timeline
        trace["agent_quality_score"] = round(float(qres.quality_score), 3)
        trace["agent_quality_pass"] = bool(qres.ok)
        trace["voice"] = ctx.voice
        rec.ai_adaptation_trace = trace
        return rec

    # ── whole project ────────────────────────────────────────────────────
    def run(
        self,
        segments: list[str],
        timing_map: Sequence[Any] | None,
        source_segments: list[str] | None,
        *,
        src_lang: str,
        tgt_lang: str,
        raw_mt_segments: list[str] | None = None,
        progress_cb=None,
    ) -> tuple[list[str], list[Any]]:
        from engines.pipeline_segment_watchdog import run_segment_bounded
        from engines.timing_aware_translation import (
            TimingAwareRecord,
            _adaptation_watchdog_timeout,
            _resolve_worker_count,
            slot_ms_from_timing,
            word_count,
        )

        total = len(segments)
        src_rows = list(source_segments or [])
        raw_rows = list(raw_mt_segments or [])
        out: list[str | None] = [None] * total
        records: list[Any] = [None] * total

        # Configure the per-segment LLM budget for this run (mirrors legacy path).
        try:
            from engines.ai_core import llm_gateway

            llm_gateway.begin_run(
                self.task_id,
                mode=self.strategy.get("speed_mode"),
                per_segment_s=self.strategy.get("per_segment_budget_s") or None,
                project_s=self.strategy.get("project_budget_s") or None,
            )
        except Exception:
            pass
        try:
            from engines.ai_core.ai_network import get_network, reset_network
            from engines.ai_core.development_lifecycle import record_stage, STAGE_DEVELOPMENT

            reset_network(self.task_id)
            get_network(self.task_id)
            record_stage(self.task_id, STAGE_DEVELOPMENT, detail="coordinator_run")
        except Exception:
            pass

        def _make_ctx(i: int, text: str) -> SegmentContext:
            raw = str(text or "")
            return SegmentContext(
                index=i,
                source_text=str(src_rows[i]) if i < len(src_rows) else "",
                raw_translation=str(raw_rows[i]) if i < len(raw_rows) else raw,
                text=raw,
                slot_ms=slot_ms_from_timing(timing_map, i),
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                task_id=self.task_id,
                profile=self.profile,
                strategy=self.strategy,
            )

        def _fallback(i: int, ctx: SegmentContext):
            raw = str(raw_rows[i] if i < len(raw_rows) else "").strip()
            cur = str(ctx.text or segments[i] or "").strip()
            text = cur or raw
            reason = "watchdog_keep_input" if cur else "watchdog_fallback_raw_mt"
            rec = TimingAwareRecord(
                index=i, source_words=word_count(ctx.source_text),
                input_words=word_count(ctx.raw_translation),
                output_words=word_count(text), slot_ms=int(ctx.slot_ms or 0),
                text_before=ctx.raw_translation, text_after=text, reason=reason,
            )
            rec.ai_adaptation_trace = {"agent_timeline": [], "watchdog_fallback": True}
            return text, rec

        def _process(i: int, text: str):
            ctx = _make_ctx(i, text)

            def _run():
                try:
                    from engines.translation_adapt import set_llm_context

                    set_llm_context(segment=i, stage="ai_agents")
                except Exception:
                    pass
                final_text, rec, _tl = self.process_segment(ctx)
                return final_text, rec

            watch = run_segment_bounded(
                task_id=self.task_id,
                phase="ai_agents_coordinator",
                segment_index=i,
                fn=_run,
                fallback=lambda: _fallback(i, ctx),
                timeout_sec=_adaptation_watchdog_timeout(),
            )
            adapted, rec = watch.value
            if watch.timed_out or watch.error:
                rec.reason = f"watchdog_{watch.error or 'timeout'}"
            out[i] = adapted
            records[i] = rec
            return rec

        import threading as _threading

        _prog = {"done": 0, "t0": time.monotonic()}
        _lock = _threading.Lock()

        def _report():
            if progress_cb is None:
                return
            with _lock:
                _prog["done"] += 1
                done = _prog["done"]
            try:
                progress_cb(done, total)
            except Exception:
                pass

        workers = _resolve_worker_count(self.strategy.get("speed_mode"), total)
        if workers <= 1 or total <= 1:
            for i, text in enumerate(segments):
                _process(i, text)
                _report()
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-agents") as ex:
                futures = {ex.submit(_process, i, t): i for i, t in enumerate(segments)}
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception:
                        i = futures[fut]
                        out[i] = str(segments[i] or "")
                        records[i] = TimingAwareRecord(
                            index=i, text_before=str(segments[i] or ""),
                            text_after=str(segments[i] or ""), reason="worker_error",
                        )
                    _report()

        for i in range(total):
            if records[i] is None:
                records[i] = TimingAwareRecord(
                    index=i, text_before=str(segments[i] or ""),
                    text_after=str(segments[i] or ""), reason="missing",
                )
            if out[i] is None:
                out[i] = str(segments[i] or "")

        logger.info(
            "[AIAgents] task=%s segments=%d workers=%d", self.task_id or "?", total, workers
        )
        return [str(x or "") for x in out], [r for r in records if r is not None]
