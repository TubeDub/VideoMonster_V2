"""Pipeline orchestrator — runs stages via contracts only."""

from __future__ import annotations

from typing import Any

from engines.pipeline_platform.contract import (
    PipelineContext,
    SegmentPipelineTrace,
    StageEnvelope,
    StageId,
    timed_run,
)
from engines.pipeline_platform.registry import bootstrap_stages, get_stage, list_stages
from engines.pipeline_platform.word_timing_bridge import wtm_from_segment_info

_STAGE_ORDER = [
    StageId.STT,
    StageId.TRANSLATION_MANAGER,
    StageId.ENTERPRISE_TRANSLATION,
    StageId.NATURAL_TRANSLATION,
    StageId.TRANSLATION_OPTIMIZER,
    StageId.TIMING_OPTIMIZER,
    StageId.TTS,
    StageId.AUDIO_BUILDER,
    StageId.FINAL_MUX,
]

_STAGE_LABELS = {
    StageId.STT: "STT",
    StageId.TRANSLATION_MANAGER: "Raw Translation",
    StageId.ENTERPRISE_TRANSLATION: "Enterprise Translation",
    StageId.NATURAL_TRANSLATION: "Natural Translation",
    StageId.TRANSLATION_OPTIMIZER: "Optimizer",
    StageId.TIMING_OPTIMIZER: "Timing",
    StageId.TTS: "TTS Text",
    StageId.AUDIO_BUILDER: "Final Audio",
    StageId.FINAL_MUX: "Mux",
}


def build_context_from_info(info: dict[str, Any], *, task_id: str = "", app_dir: str = "") -> PipelineContext:
    segments: list[str] = []
    for row in info.get("segments_data") or []:
        segments.append(str(row.get("text") or row.get("source_text") or ""))
    if not segments:
        audits = info.get("translation_audits") or []
        segments = [str(a.get("final_text") or a.get("source_text") or "") for a in audits]
    timing_map = []
    for i, seg in enumerate(segments):
        wtm = wtm_from_segment_info(info, i)
        timing_map.append(
            {
                "index": i,
                "duration_ms": int(
                    (wtm.get("segment_end_ms") or 0) - (wtm.get("segment_start_ms") or 0)
                )
                or int(seg and 2000),
            }
        )
    return PipelineContext(
        task_id=task_id or str(info.get("task_id") or ""),
        app_dir=app_dir,
        src_lang=str(info.get("detected_lang") or info.get("source_lang") or "en"),
        tgt_lang=str(info.get("target_lang") or "uk"),
        segments=segments,
        timing_map=timing_map,
        info=dict(info),
    )


def run_segment_trace(ctx: PipelineContext, index: int) -> SegmentPipelineTrace:
    bootstrap_stages()
    original = ctx.segments[index] if index < len(ctx.segments) else ""
    trace = SegmentPipelineTrace(segment_index=index, original_text=original)
    envelope = StageEnvelope(stage_id="original", segment_index=index, text_out=original)
    trace.word_timing_map = wtm_from_segment_info(ctx.info, index)

    for sid in _STAGE_ORDER:
        mod = get_stage(sid)
        if not mod:
            continue
        envelope = timed_run(mod, ctx, index, envelope)
        trace.stages.append(envelope)
        if envelope.word_timing_map:
            trace.word_timing_map = dict(envelope.word_timing_map)

    return trace


def build_platform_trace(info: dict[str, Any], *, task_id: str = "", app_dir: str = "") -> dict[str, Any]:
    ctx = build_context_from_info(info, task_id=task_id, app_dir=app_dir)
    segments_out: list[dict[str, Any]] = []
    for i in range(max(len(ctx.segments), 1)):
        if i >= len(ctx.segments) and i > 0:
            break
        trace = run_segment_trace(ctx, i)
        segments_out.append(trace.to_dict())

    return {
        "task_id": ctx.task_id,
        "trace_id": ctx.trace_id,
        "src_lang": ctx.src_lang,
        "tgt_lang": ctx.tgt_lang,
        "segment_count": len(segments_out),
        "segments": segments_out,
        "stages": list_stages(),
        "stage_order": [s.value for s in _STAGE_ORDER],
        "stage_labels": {k.value: v for k, v in _STAGE_LABELS.items()},
    }


def platform_status() -> dict[str, Any]:
    bootstrap_stages()
    return {"stages": list_stages(), "ready": True}
