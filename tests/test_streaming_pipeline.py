"""Tests for AI Core 4.2 Streaming Pipeline mode."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.streaming_pipeline import (
    PIPELINE_MODE_BATCH,
    PIPELINE_MODE_STREAMING,
    SegmentSnapshot,
    StreamingTextPipeline,
    resolve_pipeline_mode,
    streaming_stages_in_chain,
)
from engines.ai_core.orchestrator import AICoreOrchestrator


def test_snapshot_is_immutable():
    seg = {"index": 0, "text": "Hi", "translated_text": "Привіт"}
    snap = SegmentSnapshot.from_segment(seg, 0)
    seg["translated_text"] = "Changed"
    assert snap.get("translated_text") == "Привіт"


def test_resolve_pipeline_mode_defaults_batch():
    assert resolve_pipeline_mode({}) == PIPELINE_MODE_BATCH
    assert resolve_pipeline_mode({"pipeline_mode": "streaming"}) == PIPELINE_MODE_STREAMING


def test_streaming_stages_in_chain():
    stages = streaming_stages_in_chain(
        ["planner", "semantic", "timing", "grammar", "quality"]
    )
    assert stages == ("semantic", "timing", "grammar", "quality")


def test_orchestrator_injects_streaming_block():
    orch = AICoreOrchestrator()
    chain = [
        ("semantic", object, 90),
        ("timing", object, 90),
        ("grammar", object, 60),
        ("quality", object, 120),
        ("reviewer", object, 60),
        ("voice_preparation", object, 30),
        ("voice", object, 300),
    ]
    state = {"pipeline_mode": "streaming"}
    new_chain = orch._apply_streaming_mode(chain, state)
    assert new_chain[0][0] == "streaming_text"
    assert new_chain[-1][0] == "voice"
    assert len(new_chain) == 2
    assert "voice_preparation" in state.get("streaming_stages", ())
    assert "voice" not in state.get("streaming_stages", ())


def test_orchestrator_streaming_includes_voice_with_handler():
    orch = AICoreOrchestrator()
    chain = [
        ("semantic", object, 90),
        ("timing", object, 90),
        ("grammar", object, 60),
        ("voice_preparation", object, 30),
        ("voice", object, 300),
        ("voice_verification", object, 60),
    ]
    state = {"pipeline_mode": "streaming", "streaming_voice": True}
    new_chain = orch._apply_streaming_mode(chain, state)
    assert new_chain[0][0] == "streaming_text"
    assert new_chain[-1][0] == "voice_verification"
    assert "voice" in state.get("streaming_stages", ())


def test_streaming_voice_pipeline_parallel(tmp_path):
    from engines.ai_core.streaming_pipeline.voice_stage import StreamingVoicePipeline

    manifest = {"target_lang": "uk"}
    segments = [
        {"index": 0, "text_for_tts": "Привіт.", "voice_input": "Привіт."},
        {"index": 1, "text_for_tts": "Бувай.", "voice_input": "Бувай."},
    ]

    def fake_handler(list_index, segment_index, seg, manifest, state, task_id):
        out = dict(seg)
        out["tts_file_path"] = f"seg_{segment_index}.mp3"
        out["tts_text"] = out.get("text_for_tts") or ""
        out["status"] = "generated"
        return out

    state = {
        "segments": segments,
        "segment_tts_handler": fake_handler,
        "streaming_voice_workers": 2,
    }
    pipe = StreamingVoicePipeline(manifest, state, "voice-test")
    result = pipe.run()
    assert result.status == "success"
    assert result.updated_state.get("streaming_voice_done") is True
    assert result.metrics.get("tts_segments_done") == 2
    assert all(s.get("tts_file_path") for s in result.updated_state["segments"])


def test_streaming_pipeline_semantic_only(tmp_path):
    manifest = {"project_uuid": "s", "source_lang": "en", "target_lang": "uk"}
    segments = [
        {"index": 0, "text": "Hello.", "translated_text": "Привіт."},
        {"index": 1, "text": "Bye.", "translated_text": "Бувай."},
    ]
    state = {"segments": segments}

    def fake_rerun(class_name, list_index, manifest, state, task_id):
        seg = dict(state["segments"][list_index])
        if class_name == "SemanticAgent":
            seg["semantic_text"] = str(seg.get("translated_text") or "") + "!"
        elif class_name == "TimingAgent":
            seg["timing_text"] = str(seg.get("semantic_text") or "")
        elif class_name == "GrammarAgent":
            seg["grammar_text"] = str(seg.get("timing_text") or "")
        return seg

    with patch(
        "engines.ai_core.quality_agent.retry_orchestrator.rerun_agent_for_segment",
        side_effect=fake_rerun,
    ):
        pipe = StreamingTextPipeline(
            manifest, state, "stream-test", stages=("semantic", "timing", "grammar")
        )
        result = pipe.run()

    assert result.status in ("success", "warning")
    out = result.updated_state["segments"]
    assert all(str(s.get("grammar_text") or "").endswith("!") for s in out)
    assert result.metrics.get("pipeline_mode") == "streaming"
