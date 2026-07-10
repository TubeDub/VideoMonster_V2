"""Register pipeline stages as PluginHost plugins."""

from __future__ import annotations

from engines.pipeline_platform.contract import StageId
from engines.tubedub.plugin_host import PluginKind, PluginRecord, get_plugin_host


_STAGE_LABELS = {
    StageId.STT: "STT",
    StageId.TRANSLATION_MANAGER: "Translation Manager",
    StageId.ENTERPRISE_TRANSLATION: "Enterprise Translation",
    StageId.NATURAL_TRANSLATION: "Natural Translation",
    StageId.TRANSLATION_OPTIMIZER: "Translation Optimizer",
    StageId.TIMING_OPTIMIZER: "Timing Optimizer",
    StageId.TTS: "TTS",
    StageId.AUDIO_BUILDER: "Audio Builder",
    StageId.FINAL_MUX: "Final Mux",
}


def register_pipeline_stage_plugins() -> int:
    """Each pipeline stage is a plugin — no hardcoded stage wiring."""
    host = get_plugin_host()
    count = 0
    for sid in StageId:
        pid = f"pipeline.{sid.value}"

        def _make(stage_id: str):
            def _proc(payload: dict, **_kw: dict) -> dict:
                from engines.pipeline_platform.registry import bootstrap_stages, get_stage
                from engines.pipeline_platform.contract import PipelineContext, StageEnvelope, timed_run

                bootstrap_stages()
                mod = get_stage(StageId(stage_id))
                if not mod:
                    return {"error": "stage_not_found", "stage_id": stage_id}
                ctx = PipelineContext(
                    task_id=str(payload.get("task_id") or ""),
                    app_dir=str(payload.get("app_dir") or ""),
                    src_lang=str(payload.get("src_lang") or "en"),
                    tgt_lang=str(payload.get("tgt_lang") or "uk"),
                    info=dict(payload.get("info") or {}),
                )
                idx = int(payload.get("segment_index") or 0)
                env_in = StageEnvelope(
                    stage_id="input",
                    segment_index=idx,
                    text_out=str(payload.get("text_in") or ""),
                    word_timing_map=dict(payload.get("word_timing_map") or {}),
                )
                out = timed_run(mod, ctx, idx, env_in)
                return out.to_dict()

            return _proc

        host.register(
            PluginRecord(
                plugin_id=pid,
                label=_STAGE_LABELS.get(sid, sid.value),
                kind=PluginKind.PIPELINE_STAGE.value,
                backend="tubedub",
                module_id="pipeline_platform",
            ),
            processor=_make(sid.value),
        )
        count += 1
    return count
