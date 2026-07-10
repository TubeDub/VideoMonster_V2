"""Developer Mode pipeline view — Stage 2 TZ format."""

from __future__ import annotations

import json
from typing import Any

from engines.pipeline_platform.orchestrator import build_platform_trace

TZ_STAGE_KEYS = [
    ("original", "Original"),
    ("stt", "STT"),
    ("translation_manager", "Raw Translation"),
    ("enterprise_translation", "Enterprise Translation"),
    ("natural_translation", "Natural Translation"),
    ("translation_optimizer", "Optimizer"),
    ("timing_optimizer", "Timing"),
    ("tts", "TTS Text"),
    ("tts_audio", "TTS Audio"),
    ("audio_builder", "Final Audio"),
    ("final_mux", "Mux"),
]


def _stage_row(envelope: dict[str, Any] | None, *, label: str, original: str = "") -> dict[str, Any]:
    if not envelope:
        return {
            "key": label,
            "label": label,
            "text": "",
            "status": "pending",
            "processing_ms": 0,
            "duration_ms": 0,
            "engine": "",
            "rules_applied": [],
            "quality_score": None,
            "errors": [],
            "warnings": [],
            "diff_from_previous": [],
            "audio_path": "",
            "expandable": True,
        }
    diag = envelope.get("diagnostics") or {}
    text = envelope.get("text_out") or envelope.get("text_in") or ""
    return {
        "key": envelope.get("stage_id", label),
        "label": label,
        "text": text,
        "status": envelope.get("status", "ok"),
        "processing_ms": diag.get("processing_ms", 0),
        "duration_ms": diag.get("duration_ms", 0),
        "engine": diag.get("engine", ""),
        "rules_applied": list(diag.get("rules_applied") or []),
        "quality_score": diag.get("quality_score"),
        "errors": list(diag.get("errors") or []),
        "warnings": list(diag.get("warnings") or []),
        "diff_from_previous": list(diag.get("diff_from_previous") or []),
        "audio_path": envelope.get("audio_path", ""),
        "meta": dict(diag.get("meta") or {}),
        "artifacts": dict(envelope.get("artifacts") or {}),
        "word_timing_map": dict(envelope.get("word_timing_map") or {}),
        "expandable": True,
    }


def build_dev_pipeline_view(info: dict[str, Any], *, task_id: str = "", app_dir: str = "") -> dict[str, Any]:
    trace = build_platform_trace(info, task_id=task_id, app_dir=app_dir)
    labels = trace.get("stage_labels") or {}
    segments_ui: list[dict[str, Any]] = []

    for seg in trace.get("segments") or []:
        idx = int(seg.get("segment_index", 0))
        original = str(seg.get("original_text") or "")
        by_id = {s.get("stage_id"): s for s in (seg.get("stages") or [])}
        chain: list[dict[str, Any]] = [
            {
                "key": "original",
                "label": "Original",
                "text": original,
                "status": "ok",
                "processing_ms": 0,
                "duration_ms": 0,
                "engine": "",
                "rules_applied": [],
                "quality_score": 1.0,
                "errors": [],
                "warnings": [],
                "diff_from_previous": [],
                "audio_path": "",
                "expandable": True,
            }
        ]
        for sid, lbl in TZ_STAGE_KEYS[1:]:
            if sid == "tts_audio":
                tts_env = by_id.get("tts")
                chain.append(
                    _stage_row(
                        {
                            "stage_id": "tts_audio",
                            "text_out": "",
                            "status": tts_env.get("status") if tts_env else "pending",
                            "audio_path": (tts_env or {}).get("audio_path", ""),
                            "diagnostics": (tts_env or {}).get("diagnostics") or {},
                        }
                        if tts_env
                        else None,
                        label=lbl,
                    )
                )
                continue
            chain.append(_stage_row(by_id.get(sid), label=labels.get(sid, lbl), original=original))
        segments_ui.append(
            {
                "segment_index": idx,
                "original_text": original,
                "chain": chain,
                "word_timing_map": dict(seg.get("word_timing_map") or {}),
            }
        )

    return {
        "task_id": trace.get("task_id"),
        "trace_id": trace.get("trace_id"),
        "segment_count": len(segments_ui),
        "segments": segments_ui,
        "stages_meta": trace.get("stages"),
        "copy_text": export_pipeline_log_text(segments_ui, trace),
    }


def export_pipeline_log_text(segments_ui: list[dict[str, Any]], trace: dict[str, Any]) -> str:
    lines: list[str] = [
        f"TubeDub Pipeline Trace — task={trace.get('task_id')} trace={trace.get('trace_id')}",
        f"langs: {trace.get('src_lang')} → {trace.get('tgt_lang')}",
        "",
    ]
    for seg in segments_ui:
        lines.append(f"=== Segment {seg.get('segment_index')} ===")
        lines.append(f"Original: {seg.get('original_text')}")
        for stage in seg.get("chain") or []:
            lines.append(f"  [{stage.get('label')}] status={stage.get('status')} engine={stage.get('engine')}")
            lines.append(f"    text: {stage.get('text')}")
            lines.append(f"    time: {stage.get('processing_ms')}ms duration: {stage.get('duration_ms')}ms")
            if stage.get("quality_score") is not None:
                lines.append(f"    quality: {stage.get('quality_score')}")
            if stage.get("rules_applied"):
                lines.append(f"    rules: {', '.join(stage['rules_applied'])}")
            if stage.get("warnings"):
                lines.append(f"    warnings: {', '.join(stage['warnings'])}")
            if stage.get("errors"):
                lines.append(f"    errors: {', '.join(stage['errors'])}")
            if stage.get("audio_path"):
                lines.append(f"    audio: {stage.get('audio_path')}")
        lines.append("")
    return "\n".join(lines)


def export_pipeline_log_json(view: dict[str, Any]) -> str:
    return json.dumps(view, ensure_ascii=False, indent=2)
