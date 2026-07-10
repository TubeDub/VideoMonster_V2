"""AI Core 4.2 — Streaming Pipeline mode."""

from engines.ai_core.streaming_pipeline.mode import (
    AI_CORE_VERSION_STREAMING,
    PIPELINE_MODE_BATCH,
    PIPELINE_MODE_STREAMING,
    resolve_pipeline_mode,
    streaming_stages_in_chain,
)
from engines.ai_core.streaming_pipeline.pipeline import (
    StreamingTextPipeline,
    StreamingTextPipelineRunner,
    run_streaming_text_pipeline,
)
from engines.ai_core.streaming_pipeline.snapshot import SegmentSnapshot
from engines.ai_core.streaming_pipeline.voice_stage import (
    StreamingVoicePipeline,
    process_voice_segment,
)

__all__ = [
    "AI_CORE_VERSION_STREAMING",
    "PIPELINE_MODE_BATCH",
    "PIPELINE_MODE_STREAMING",
    "SegmentSnapshot",
    "StreamingTextPipeline",
    "StreamingTextPipelineRunner",
    "StreamingVoicePipeline",
    "process_voice_segment",
    "resolve_pipeline_mode",
    "run_streaming_text_pipeline",
    "streaming_stages_in_chain",
]
