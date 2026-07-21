"""StreamDub async pipeline orchestrator — queues between stages."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from engines.streamdub.artifacts.benchmark import (
    TimelineRecorder,
    write_performance_report,
    write_quality_report,
    write_timeline,
)
from engines.streamdub.memory.entity_manager import EntityManager
from engines.streamdub.memory.project_memory import ProjectMemory
from engines.streamdub.memory.translation_memory import TranslationMemory
from engines.streamdub.modules.fast_translation import FastTranslationEngine
from engines.streamdub.modules.lip_sync import LipSyncEngine
from engines.streamdub.modules.llm_refiner import LLMRefiner
from engines.streamdub.modules.quality_analyzer import QualityAnalyzer
from engines.streamdub.modules.segmenter import SmartSegmenter
from engines.streamdub.modules.tts_engine import TTSEngine
from engines.streamdub.modules.video_merge import VideoMergeEngine
from engines.streamdub.modules.voice_clone import VoiceCloneEngine
from engines.streamdub.modules.whisper_engine import WhisperEngine
from engines.streamdub.pipeline.modes import stages_for_mode
from engines.streamdub.types import StreamDubMode, StreamDubRequest, StreamDubResult, StreamSegment

logger = logging.getLogger("tubedub.streamdub.orchestrator")

_MODULE_FACTORIES = {
    "whisper": WhisperEngine,
    "segmenter": SmartSegmenter,
    "fast_translation": FastTranslationEngine,
    "quality_analyzer": QualityAnalyzer,
    "llm_refiner": LLMRefiner,
    "tts": TTSEngine,
    "voice_clone": VoiceCloneEngine,
    "lip_sync": LipSyncEngine,
    "video_merge": VideoMergeEngine,
}


class StreamDubOrchestrator:
    """Conductor — loads modules once, routes by mode, async stage queues."""

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = Path(app_dir)
        self._modules: dict[str, Any] = {}
        self._initialized = False

    def initialize(self, config: dict[str, Any] | None = None) -> None:
        if self._initialized:
            return
        cfg = config or {}
        for name, cls in _MODULE_FACTORIES.items():
            mod = cls()
            mod.initialize(app_dir=self.app_dir, config=cfg)
            self._modules[name] = mod
        self._initialized = True
        logger.info("[StreamDub] initialized %d modules", len(self._modules))

    def shutdown(self) -> None:
        for mod in self._modules.values():
            try:
                mod.shutdown()
            except Exception:
                pass
        self._modules.clear()
        self._initialized = False

    def health_check(self) -> dict[str, Any]:
        return {
            name: mod.health_check().to_dict()
            for name, mod in self._modules.items()
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            name: mod.capabilities().to_dict()
            for name, mod in self._modules.items()
        }

    def _run_stage(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        mod = self._modules.get(name)
        if mod is None:
            raise KeyError(f"unknown stage: {name}")
        return mod.process(payload)

    async def _run_stage_async(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._run_stage, name, payload)

    async def run(self, request: StreamDubRequest) -> StreamDubResult:
        if not self._initialized:
            self.initialize()

        project_id = request.project_id or uuid.uuid4().hex
        timeline = TimelineRecorder()
        stage_timings: dict[str, float] = {}
        errors: list[str] = []

        tm = TranslationMemory(project_id, self.app_dir)
        entities = EntityManager(project_id, self.app_dir)
        memory = ProjectMemory(project_id, self.app_dir)
        memory.load()

        stages = stages_for_mode(request.mode)
        ctx: dict[str, Any] = {
            "project_id": project_id,
            "video_path": request.video_path,
            "audio_path": request.audio_path or request.video_path,
            "source_lang": request.source_lang,
            "target_lang": request.target_lang,
            "voice": request.voice,
            "model_size": request.model_size,
            "mt_backend": request.mt_backend,
            "translation_memory": tm,
            "entity_manager": entities,
            "project_memory": memory,
            "segments": [],
            "detected_lang": "",
            "output_audio": "",
            "output_video": "",
        }

        if request.mode == StreamDubMode.CINEMA:
            ctx["force_llm_all"] = True

        for stage in stages:
            timeline.start(stage)
            t0 = time.perf_counter()
            try:
                if stage == "whisper":
                    out = await self._run_stage_async(stage, ctx)
                    ctx["segments"] = out.get("segments") or []
                    ctx["detected_lang"] = out.get("detected_lang") or ""
                    entities.extract_from_segments([s.text for s in ctx["segments"]])
                elif stage == "segmenter":
                    out = await self._run_stage_async(stage, {**ctx, "max_tokens_per_segment": request.max_tokens_per_segment})
                    ctx["segments"] = out.get("segments") or ctx["segments"]
                elif stage == "fast_translation":
                    out = await self._run_stage_async(stage, ctx)
                    ctx["segments"] = out.get("segments") or ctx["segments"]
                    ctx["mt_stats"] = out
                elif stage == "quality_analyzer":
                    out = await self._run_stage_async(stage, ctx)
                    ctx["segments"] = out.get("segments") or ctx["segments"]
                    ctx["quality_stats"] = out
                elif stage == "llm_refiner":
                    out = await self._run_stage_async(stage, ctx)
                    ctx["segments"] = out.get("segments") or ctx["segments"]
                    ctx["llm_stats"] = out
                elif stage == "tts":
                    out = await self._run_stage_async(stage, ctx)
                    ctx["segments"] = out.get("segments") or ctx["segments"]
                    files = out.get("tts_files") or []
                    if files:
                        ctx["output_audio"] = str(files[0])
                elif stage in ("voice_clone", "lip_sync"):
                    out = await self._run_stage_async(stage, ctx)
                    ctx.update({k: v for k, v in out.items() if k not in ctx or k.startswith(stage)})
                elif stage == "video_merge":
                    out = await self._run_stage_async(stage, ctx)
                    ctx["output_video"] = out.get("output_video") or ""
                else:
                    out = await self._run_stage_async(stage, ctx)
                    ctx.update(out)
            except Exception as exc:
                logger.exception("[StreamDub] stage %s failed", stage)
                errors.append(f"{stage}: {exc}")
                timeline.end(stage, time.perf_counter() - t0, error=str(exc))
                break

            elapsed = time.perf_counter() - t0
            stage_timings[stage] = elapsed
            timeline.end(stage, elapsed)

        segments: list[StreamSegment] = ctx.get("segments") or []
        tm.save()
        entities.save()
        memory.save()

        stats = {
            "mode": request.mode.value,
            "segments": len(segments),
            "translation_memory": tm.stats(),
            "entities": len(entities.to_dict()),
            "mt_stats": ctx.get("mt_stats") or {},
            "quality_stats": ctx.get("quality_stats") or {},
            "llm_stats": ctx.get("llm_stats") or {},
        }

        perf_path = write_performance_report(
            self.app_dir,
            project_id,
            mode=request.mode,
            stage_timings=stage_timings,
            stats=stats,
            success=not errors,
        )
        tl_path = write_timeline(self.app_dir, project_id, timeline.events())
        qr_path = write_quality_report(
            self.app_dir,
            project_id,
            segments,
            mode=request.mode,
            stats=stats,
        )

        return StreamDubResult(
            project_id=project_id,
            mode=request.mode,
            success=not errors,
            segments=segments,
            detected_lang=str(ctx.get("detected_lang") or ""),
            output_audio=str(ctx.get("output_audio") or ""),
            output_video=str(ctx.get("output_video") or ""),
            stats=stats,
            artifacts={
                "performance_report_json": perf_path,
                "timeline_json": tl_path,
                "quality_report_json": qr_path,
            },
            errors=errors,
        )
