"""AI Core 3.0 — Project-level agents: Planner, Voice, Mix.

These three do not rewrite segment text:

* :class:`PlannerAgent` runs ONCE per project and produces the project session
  (language, content type, speaker count, speech rate, complexity, recommended
  mode) by reusing :func:`engines.ai_core.project_analysis.analyze_project` and
  :func:`engines.ai_core.strategy.build_strategy`.
* :class:`VoiceAgent` runs per segment and decides delivery (timbre/emotion/
  speed/intonation) by reusing :func:`engines.ai_core.voice_director.decide_voice`.
* :class:`MixAgent` runs once at the end and records the mixing plan (music /
  ambience / ducking / final MP4). The actual mux stays in the auto-dub pipeline
  — the agent only records the decision for the timeline.
"""

from __future__ import annotations

from typing import Any

from engines.ai_core.agents.base import Agent, AgentResult, SegmentContext


class PlannerAgent(Agent):
    """Project Planner → project session. Cheap heuristics only (no LLM)."""

    name = "planner"

    def analyze_profile(
        self,
        *,
        source_segments,
        timing_map=None,
        src_lang: str = "",
        tgt_lang: str = "",
        content_mode_hint: str | None = None,
    ) -> dict[str, Any]:
        """Build the project profile dict for the whole project."""
        from engines.ai_core.project_analysis import analyze_project

        profile = analyze_project(
            source_segments=source_segments,
            timing_map=timing_map,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            content_mode_hint=content_mode_hint,
        )
        return profile.to_dict()

    def build_plan(
        self,
        profile,
        *,
        requested_mode: str | None = None,
    ) -> dict[str, Any]:
        """Build the project strategy dict from the planner profile."""
        from engines.ai_core import llm_gateway
        from engines.ai_core.project_analysis import ProjectProfile
        from engines.ai_core.strategy import build_strategy

        if isinstance(profile, dict):
            prof = ProjectProfile(**{
                k: v for k, v in profile.items() if k in ProjectProfile().__dict__
            })
        else:
            prof = profile
        strategy = build_strategy(
            prof,
            requested_mode=requested_mode,
            llm_available=llm_gateway.is_available(),
            model=llm_gateway.active_model(),
        )
        return strategy.to_dict()

    def analyze(
        self,
        *,
        source_segments,
        translated_segments=None,
        timing_map=None,
        src_lang: str = "",
        tgt_lang: str = "",
        content_mode_hint: str | None = None,
        requested_mode: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Backward-compatible helper returning (profile, strategy)."""
        profile = self.analyze_profile(
            source_segments=source_segments,
            timing_map=timing_map,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            content_mode_hint=content_mode_hint,
        )
        strategy = self.build_plan(profile, requested_mode=requested_mode)
        return profile, strategy

    def _run(self, ctx: SegmentContext) -> AgentResult:
        # Planner is project-level; per-segment it is a no-op confirmation.
        return AgentResult(
            agent=self.name,
            text=ctx.text,
            changed=False,
            skipped=True,
            reason="project_level",
            diagnostics={
                "content_type": ctx.profile.get("content_type"),
                "mode": ctx.strategy.get("speed_mode"),
            },
        )


class VoiceAgent(Agent):
    """Voice Agent → timbre, emotion, speed, intonation, TTS mode. No LLM."""

    name = "voice"

    def _run(self, ctx: SegmentContext) -> AgentResult:
        from engines.ai_core.project_analysis import ProjectProfile
        from engines.ai_core.voice_director import decide_voice

        profile = ProjectProfile(**{
            k: v for k, v in ctx.profile.items()
            if k in ProjectProfile().__dict__
        }) if ctx.profile else ProjectProfile()
        seg_emotion = ctx.diagnostics.get("segment_emotion")
        direction = decide_voice(profile, segment_emotion=seg_emotion)
        ctx.voice = direction.to_dict()
        return AgentResult(
            agent=self.name,
            text=ctx.text,
            changed=False,
            reason="voice_decided",
            diagnostics={"voice": ctx.voice},
        )


class MixAgent(Agent):
    """Mix Agent → mixing plan (music/ambience/effects/final MP4). No LLM.

    The heavy mux lives in the auto-dub pipeline; this agent only records the
    plan AI Core decided so the timeline is complete and future modules can
    reuse the decision.
    """

    name = "mix"

    def plan(self, strategy: dict[str, Any]) -> dict[str, Any]:
        return {
            "preserve_music": bool(strategy.get("preserve_music", True)),
            "ducking_enabled": bool(strategy.get("ducking_enabled", True)),
            "output": "final_mp4",
        }

    def _run(self, ctx: SegmentContext) -> AgentResult:
        return AgentResult(
            agent=self.name,
            text=ctx.text,
            changed=False,
            skipped=True,
            reason="project_level",
            diagnostics={"mix_plan": self.plan(ctx.strategy)},
        )
