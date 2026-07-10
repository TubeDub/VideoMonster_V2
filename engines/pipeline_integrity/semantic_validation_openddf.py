"""
OpenDDF integration for SemanticValidationError — rich snapshots and diffs.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_WORD_RE = re.compile(r"\w+(?:['']\w+)?", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").lower())


def _find_entity_position(source: str, entity: str) -> dict[str, Any]:
    src = str(source or "")
    ent = str(entity or "").strip()
    if not ent or not src:
        return {"char_start": -1, "char_end": -1, "context": ""}
    idx = src.lower().find(ent.lower())
    if idx < 0:
        return {"char_start": -1, "char_end": -1, "context": src[:120]}
    start = max(0, idx - 40)
    end = min(len(src), idx + len(ent) + 40)
    return {
        "char_start": idx,
        "char_end": idx + len(ent),
        "context": src[start:end],
    }


def _word_diff(before: str, after: str) -> dict[str, Any]:
    bw = _tokenize(before)
    aw = _tokenize(after)
    sm = SequenceMatcher(None, bw, aw)
    removed: list[str] = []
    added: list[str] = []
    replaced: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            removed.extend(bw[i1:i2])
        elif tag == "insert":
            added.extend(aw[j1:j2])
        elif tag == "replace":
            old_chunk = " ".join(bw[i1:i2])
            new_chunk = " ".join(aw[j1:j2])
            if old_chunk or new_chunk:
                replaced.append({"from": old_chunk, "to": new_chunk})
    return {
        "removed_words": removed[:50],
        "added_words": added[:50],
        "replaced": replaced[:30],
        "removed_count": len(removed),
        "added_count": len(added),
        "replaced_count": len(replaced),
    }


def _suspected_module(
    *,
    timing_adapted: bool,
    semantic_adapted: bool,
    naturalizer_changed: bool,
) -> str:
    if timing_adapted:
        return "timing_aware_translation"
    if semantic_adapted:
        return "semantic_optimizer"
    if naturalizer_changed:
        return "natural_translation"
    return "semantic_translation"


def build_entity_loss_reports(
    *,
    original: str,
    raw_mt: str,
    semantic: str,
    final: str,
    entity_errors: list[dict[str, Any]],
    timing_adapted: bool = False,
    semantic_adapted: bool = False,
    naturalizer_changed: bool = False,
) -> list[dict[str, Any]]:
    """Detailed entity loss report (TZ §5)."""
    module = _suspected_module(
        timing_adapted=timing_adapted,
        semantic_adapted=semantic_adapted,
        naturalizer_changed=naturalizer_changed,
    )
    reports: list[dict[str, Any]] = []
    for err in entity_errors:
        ent = str(err.get("value") or "")
        pos = _find_entity_position(original, ent)
        reports.append(
            {
                "lost_entity": ent,
                "entity_type": err.get("category")
                or err.get("entity_type")
                or "named_entity",
                "original_position": pos,
                "original_text": original,
                "raw_mt": raw_mt,
                "semantic_output": semantic,
                "final_output": final,
                "removal_reason": err.get("reason")
                or "entity_not_present_in_semantic_output",
                "suspected_module": module,
                "severity": "error",
            }
        )
    return reports


def build_segment_snapshot(
    *,
    index: int,
    original: str,
    raw_mt: str,
    semantic: str,
    final: str,
    source_lang: str = "",
    target_lang: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "original": original,
        "raw_mt": raw_mt,
        "semantic_output": semantic,
        "final_output": final,
        "meta": dict(meta or {}),
    }


def build_semantic_failure_record(
    *,
    index: int,
    reason: str,
    original: str,
    raw_mt: str,
    naturalized: str,
    semantic: str,
    final: str,
    chain_details: dict[str, Any],
    qa: dict[str, Any],
    source_lang: str = "",
    target_lang: str = "",
    timing_adapted: bool = False,
    per_index_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One problematic segment — full chain + entity reports."""
    entity_errors = list(chain_details.get("entity_errors") or [])
    nat_changed = bool(naturalized.strip() and naturalized.strip() != raw_mt.strip())
    entity_reports = build_entity_loss_reports(
        original=original,
        raw_mt=raw_mt,
        semantic=semantic,
        final=final,
        entity_errors=entity_errors,
        timing_adapted=timing_adapted,
        semantic_adapted=bool(chain_details.get("semantic_adapted")),
        naturalizer_changed=nat_changed,
    )
    diff_raw_sem = _word_diff(raw_mt or naturalized, semantic)
    diff_sem_final = _word_diff(semantic, final)
    diff_source_sem = _word_diff(original, semantic)
    change_reasons = list(chain_details.get("change_reasons") or [])

    transformation_chain = {
        "original": original,
        "original_english": original,
        "raw_mt": raw_mt,
        "semantic": semantic,
        "semantic_translation": semantic,
        "final": final,
        "final_tts_text": final,
    }

    errors: list[dict[str, Any]] = []
    for ent in entity_reports:
        errors.append(
            {
                "code": reason,
                "entity_name": ent["lost_entity"],
                "entity_type": ent["entity_type"],
                "cause": ent["removal_reason"],
                "location": ent["original_position"],
                "segment_index": index,
                "original_text": original,
                "changed_text": semantic,
                "final_text": final,
                "suspected_module": ent["suspected_module"],
            }
        )
    if not errors:
        errors.append(
            {
                "code": reason,
                "entity_name": "",
                "entity_type": "",
                "cause": chain_details.get("meaning_loss_score"),
                "location": {},
                "segment_index": index,
                "original_text": original,
                "changed_text": semantic,
                "final_text": final,
                "suspected_module": _suspected_module(
                    timing_adapted=timing_adapted,
                    semantic_adapted=False,
                    naturalizer_changed=nat_changed,
                ),
            }
        )

    validation_metrics = {
        k: chain_details.get(k)
        for k in (
            "meaning_preservation_score",
            "meaning_loss_score",
            "entity_preservation_score",
            "fact_preservation_score",
            "naturalness_score",
            "readability_score",
            "compression_ratio",
            "aggregate_score",
            "raw_mt_divergence",
        )
        if chain_details.get(k) is not None
    }

    return {
        "index": index,
        "reason": reason,
        "transformation_chain": transformation_chain,
        "chain_details": chain_details,
        "quality_analysis": qa,
        "validation_metrics": validation_metrics,
        "change_reasons": change_reasons,
        "word_diff": {
            "original_to_semantic": diff_source_sem,
            "raw_mt_to_semantic": diff_raw_sem,
            "semantic_to_final": diff_sem_final,
            "baseline_to_final": diff_source_sem,  # Changed to explicitly use original_to_semantic as per TZ
        },
        "entity_loss_reports": entity_reports,
        "errors": errors,
        "raw_mt_meta": dict(per_index_meta or {}),
        "source_lang": source_lang,
        "target_lang": target_lang,
    }


def build_semantic_failure_payload(
    chain_failures: list[dict[str, Any]],
    *,
    segments: list[str],
    raw_by_index: list[str],
    post_naturalizer: list[str],
    naturalized: list[str],
    source_lang: str = "",
    target_lang: str = "",
    per_index_meta: dict[int, dict[str, Any]] | None = None,
    timing_aware_records: list | None = None,
    pipeline_stages: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate payload attached to SemanticValidationError.details."""
    meta_map = per_index_meta or {}
    tat_by_idx = {int(getattr(r, "index", -1)): r for r in (timing_aware_records or [])}
    problem_segments: list[dict[str, Any]] = []

    for fail in chain_failures:
        idx = int(fail.get("index", -1))
        original = str(segments[idx] if 0 <= idx < len(segments) else "")
        raw_mt = str(raw_by_index[idx] if 0 <= idx < len(raw_by_index) else "")
        nat = str(post_naturalizer[idx] if 0 <= idx < len(post_naturalizer) else "")
        sem = str(naturalized[idx] if 0 <= idx < len(naturalized) else "")
        tat = tat_by_idx.get(idx)
        timing_adapted = bool(getattr(tat, "adapted", False)) if tat else False

        record = build_semantic_failure_record(
            index=idx,
            reason=str(fail.get("reason") or "unknown"),
            original=original,
            raw_mt=raw_mt,
            naturalized=nat,
            semantic=sem,
            final=sem,
            chain_details=dict(fail.get("details") or {}),
            qa=dict(fail.get("qa") or {}),
            source_lang=source_lang,
            target_lang=target_lang,
            timing_adapted=timing_adapted,
            per_index_meta=meta_map.get(idx, {}),
        )
        problem_segments.append(record)

    before_snapshots = [
        build_segment_snapshot(
            index=i,
            original=str(segments[i] if i < len(segments) else ""),
            raw_mt=str(raw_by_index[i] if i < len(raw_by_index) else ""),
            semantic=str(post_naturalizer[i] if i < len(post_naturalizer) else ""),
            final=str(post_naturalizer[i] if i < len(post_naturalizer) else ""),
            source_lang=source_lang,
            target_lang=target_lang,
            meta=meta_map.get(i),
        )
        for i in range(len(segments))
    ]
    after_snapshots = [
        build_segment_snapshot(
            index=i,
            original=str(segments[i] if i < len(segments) else ""),
            raw_mt=str(raw_by_index[i] if i < len(raw_by_index) else ""),
            semantic=str(naturalized[i] if i < len(naturalized) else ""),
            final=str(naturalized[i] if i < len(naturalized) else ""),
            source_lang=source_lang,
            target_lang=target_lang,
            meta=meta_map.get(i),
        )
        for i in range(len(segments))
    ]

    return {
        "failures": problem_segments,
        "problem_segment_indices": [p["index"] for p in problem_segments],
        "snapshot_before": before_snapshots,
        "snapshot_after": after_snapshots,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "pipeline_stages": pipeline_stages or {},
    }


def build_pipeline_stage_report(
    *,
    raw_by_index: list[str],
    post_naturalizer: list[str],
    naturalized: list[str],
    timing_map: list | None,
    timing_aware_records: list | None,
    natural_translation_enabled: bool = True,
) -> dict[str, Any]:
    """
    Stage execution manifest with skip reasons for OpenDDF runtime_pipeline.json.
    """
    n = max(len(raw_by_index), len(post_naturalizer), len(naturalized))
    nat_changed = 0
    for i in range(n):
        raw = str(raw_by_index[i] if i < len(raw_by_index) else "").strip()
        nat = str(post_naturalizer[i] if i < len(post_naturalizer) else "").strip()
        if raw and nat and raw != nat:
            nat_changed += 1

    nat_executed = bool(natural_translation_enabled and n > 0)
    if not natural_translation_enabled:
        nat_skip = "disabled_by_config"
    elif n <= 0:
        nat_skip = "empty_input"
    elif nat_changed == 0:
        nat_skip = "no_changes_needed"
    else:
        nat_skip = None

    records = list(timing_aware_records or [])
    tat_executed = bool(timing_map)
    tat_adapted = sum(1 for r in records if getattr(r, "adapted", False))
    if not timing_map:
        tat_skip = "no_timing_map"
    elif not records:
        tat_skip = "empty_input"
    elif tat_adapted == 0:
        tat_skip = "fits_without_change"
    else:
        tat_skip = None

    return {
        "natural_translation": {
            "enabled": natural_translation_enabled,
            "executed": nat_executed,
            "applied": nat_changed > 0,
            "segments_total": n,
            "segments_changed": nat_changed,
            "skip_reason": nat_skip,
        },
        "timing_aware_translation": {
            "enabled": True,
            "executed": tat_executed,
            "applied": tat_adapted > 0,
            "segments_total": len(records) if records else len(timing_map or []),
            "segments_adapted": tat_adapted,
            "skip_reason": tat_skip,
        },
        "semantic_validation": {
            "enabled": True,
            "executed": True,
            "applied": True,
            "skip_reason": None,
        },
    }


def build_runtime_pipeline(
    task_info: dict[str, Any] | None,
    *,
    validation_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Runtime engine manifest for OpenDDF (TZ §8)."""
    info = dict(task_info or {})
    payload = dict(validation_payload or {})
    audits = info.get("translation_audits") or []
    engines_used = sorted(
        {str(a.get("engine") or "") for a in audits if a.get("engine")}
    )
    if not engines_used:
        for snap in payload.get("snapshot_before") or []:
            meta = snap.get("meta") or {}
            eng = str(meta.get("engine") or "")
            if eng:
                engines_used.append(eng)
        engines_used = sorted(set(engines_used))
    routes_used = sorted(
        {
            str(a.get("route_label") or a.get("route") or "")
            for a in audits
            if a.get("route_label") or a.get("route")
        }
    )

    stages = dict(info.get("pipeline_stages") or payload.get("pipeline_stages") or {})
    if not stages:
        stages = build_pipeline_stage_report(
            raw_by_index=[str(a.get("raw_translation") or "") for a in audits],
            post_naturalizer=[str(a.get("naturalized_text") or "") for a in audits],
            naturalized=[
                str(a.get("final_text") or a.get("tts_text") or "") for a in audits
            ],
            timing_map=info.get("timing_map") or info.get("timing_map_backup"),
            timing_aware_records=None,
            natural_translation_enabled=True,
        )
        if audits:
            stages["natural_translation"]["applied"] = any(
                a.get("naturalizer_applied") for a in audits
            )
            stages["natural_translation"]["executed"] = any(
                a.get("naturalizer_executed", True) for a in audits
            )
            stages["timing_aware_translation"]["applied"] = any(
                a.get("timing_aware_applied") for a in audits
            )
            stages["timing_aware_translation"]["executed"] = any(
                a.get("timing_aware_executed") for a in audits
            )

    nat_stage = stages.get("natural_translation") or {}
    tat_stage = stages.get("timing_aware_translation") or {}

    return {
        "whisper": {
            "engine": info.get("stt_engine") or "faster-whisper",
            "model": info.get("stt_model") or info.get("model_size") or "",
            "segmentation_mode": info.get("segmentation_mode") or "timing",
            "executed": bool(info.get("source_segments")),
            "skip_reason": None if info.get("source_segments") else "empty_input",
        },
        "translation_engine": {
            "method": info.get("translate_method") or "",
            "engines": engines_used,
            "routes": routes_used,
            "source_lang": info.get("detected_lang") or info.get("source_lang") or "",
            "target_lang": info.get("target_lang") or "",
            "executed": bool(audits or payload.get("snapshot_before")),
            "skip_reason": (
                None if audits or payload.get("snapshot_before") else "not_run"
            ),
        },
        "natural_translation": {
            "enabled": nat_stage.get("enabled", True),
            "executed": bool(nat_stage.get("executed")),
            "applied": bool(nat_stage.get("applied")),
            "segments_changed": nat_stage.get("segments_changed", 0),
            "skip_reason": nat_stage.get("skip_reason"),
        },
        "semantic_engine": {
            "modules": [
                "semantic_translation",
                "semantic_optimizer",
                "semantic_meaning",
                "timing_aware_translation",
            ],
            "executed": True,
            "skip_reason": None,
        },
        "timing_aware_translation": {
            "enabled": tat_stage.get("enabled", True),
            "executed": bool(tat_stage.get("executed")),
            "applied": bool(tat_stage.get("applied")),
            "segments_adapted": tat_stage.get("segments_adapted", 0),
            "skip_reason": tat_stage.get("skip_reason"),
        },
        "qa_engine": {
            "mode": "advisory",
            "semantic_validation": True,
            "executed": True,
            "skip_reason": None,
        },
        "tts_engine": {
            "engine_id": info.get("tts_engine_id")
            or info.get("tts_engine")
            or "edge-offline",
            "voice": info.get("voice") or info.get("tts_voice") or "",
            "rate": info.get("tts_rate"),
            "pitch": info.get("tts_pitch"),
            "executed": bool(info.get("tts_files")),
            "skip_reason": None if info.get("tts_files") else "not_reached",
        },
        "voice": info.get("voice") or info.get("tts_voice") or "",
        "ssml": {
            "enabled": bool(info.get("prosody_applied") or info.get("tts_prosody")),
            "prosody": info.get("style_prosody") or info.get("prosody"),
            "executed": bool(info.get("prosody_applied")),
            "skip_reason": None if info.get("prosody_applied") else "not_reached",
        },
        "phoneme_support": {
            "enabled": bool(info.get("phonetics_applied")),
            "stress_marks": bool(info.get("stress_applied")),
            "executed": bool(
                info.get("phonetics_applied") or info.get("stress_applied")
            ),
            "skip_reason": None if info.get("phonetics_applied") else "not_reached",
        },
        "slot_fit": {
            "enabled": bool((info.get("slot_fit_stats") or {}).get("enabled", True)),
            "stats": info.get("slot_fit_stats") or {},
            "executed": bool(info.get("slot_fit_stats")),
            "skip_reason": None if info.get("slot_fit_stats") else "not_reached",
        },
        "timing_engine": {
            "soft_sync": info.get("soft_sync_enabled"),
            "word_timing": bool(
                info.get("word_timing_maps") or info.get("source_word_maps")
            ),
            "executed": bool(
                info.get("timed_audio_path") or info.get("slot_fit_stats")
            ),
            "skip_reason": None if info.get("slot_fit_stats") else "not_reached",
        },
        "pipeline_stages": stages,
        "model_versions": {
            "stt_model": info.get("stt_model") or info.get("model_size") or "",
            "translate_model": info.get("translate_model") or "",
            "llm_model": info.get("llm_model") or "",
            "tts_engine_id": info.get("tts_engine_id") or "",
        },
        "pipeline_integrity": info.get("pipeline_integrity") or {},
    }


_RUNTIME_STAGE_LABELS: dict[str, str] = {
    "whisper": "Whisper STT",
    "translation_engine": "Translation",
    "natural_translation": "Natural Translation",
    "semantic_engine": "Semantic Engine",
    "timing_aware_translation": "Timing-Aware Translation",
    "qa_engine": "QA Engine",
    "tts_engine": "TTS",
    "ssml": "SSML / Prosody",
    "phoneme_support": "Phoneme Support",
    "slot_fit": "Slot Fit",
    "timing_engine": "Timing Engine",
}


def summarize_runtime_stage(name: str, stage: dict[str, Any]) -> dict[str, Any]:
    """Structured stage row for OpenDDF UI (executed / applied / skip_reason)."""
    if not isinstance(stage, dict):
        stage = {}
    enabled = stage.get("enabled", True)
    executed = bool(stage.get("executed"))
    has_applied = "applied" in stage
    applied = bool(stage.get("applied")) if has_applied else executed
    skip_reason = stage.get("skip_reason")

    if not enabled:
        status = "disabled"
    elif not executed:
        status = "not_run"
    elif skip_reason:
        status = "skipped"
    elif applied:
        status = "applied"
    elif executed:
        status = "executed"
    else:
        status = "not_run"

    return {
        "key": name,
        "label": _RUNTIME_STAGE_LABELS.get(name, name),
        "enabled": enabled,
        "executed": executed,
        "applied": applied,
        "status": status,
        "skip_reason": skip_reason,
        "segments_changed": stage.get("segments_changed"),
        "segments_adapted": stage.get("segments_adapted"),
    }


def summarize_runtime_pipeline(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered stage summary for report.json / developer UI."""
    order = list(_RUNTIME_STAGE_LABELS.keys())
    rows: list[dict[str, Any]] = []
    for key in order:
        if key in runtime:
            rows.append(summarize_runtime_stage(key, runtime.get(key) or {}))
    for key, val in runtime.items():
        if key in order or key in {
            "pipeline_stages",
            "model_versions",
            "pipeline_integrity",
            "voice",
        }:
            continue
        if isinstance(val, dict) and ("executed" in val or "enabled" in val):
            rows.append(summarize_runtime_stage(key, val))
    return rows


def format_runtime_pipeline_block(runtime: dict[str, Any]) -> str:
    """
    Human-readable pipeline manifest for OpenDDF «Подробнее» panels.
    Shows executed vs skipped stages with skip_reason codes.
    """
    rows = summarize_runtime_pipeline(runtime)
    lines = ["Runtime pipeline stages:"]
    for row in rows:
        label = row.get("label") or row.get("key")
        status = row.get("status") or "?"
        executed = "yes" if row.get("executed") else "no"
        applied = "yes" if row.get("applied") else "no"
        skip = row.get("skip_reason")
        line = f"• {label}: executed={executed}, applied={applied}, status={status}"
        if skip:
            line += f", skip_reason={skip}"
        lines.append(line)
    nested = runtime.get("pipeline_stages") or {}
    if nested:
        lines.append("")
        lines.append("Translation sub-stages:")
        for sub_key in (
            "natural_translation",
            "timing_aware_translation",
            "semantic_validation",
        ):
            sub = nested.get(sub_key) or {}
            if not sub:
                continue
            sub_row = summarize_runtime_stage(sub_key, sub)
            skip = sub_row.get("skip_reason")
            lines.append(
                f"  · {sub_row.get('label')}: executed={'yes' if sub_row.get('executed') else 'no'}, "
                f"applied={'yes' if sub_row.get('applied') else 'no'}"
                + (f", skip_reason={skip}" if skip else "")
            )
    return "\n".join(lines)


def build_semantic_snapshot_diff(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Rich snapshot_diff for SemanticValidationError (TZ §4)."""
    failures = list(payload.get("failures") or [])
    segments_diff: list[dict[str, Any]] = []
    all_entity_reports: list[dict[str, Any]] = []

    for fail in failures:
        idx = fail.get("index")
        chain = fail.get("transformation_chain") or {}
        wd = fail.get("word_diff") or {}
        entity_reports = list(fail.get("entity_loss_reports") or [])
        all_entity_reports.extend(entity_reports)
        segments_diff.append(
            {
                "index": idx,
                "reason": fail.get("reason"),
                "transformation_chain": chain,
                "diagnostic_chain": {
                    "original_english": chain.get("original")
                    or chain.get("original_english"),
                    "raw_mt": chain.get("raw_mt"),
                    "semantic_translation": chain.get("semantic")
                    or chain.get("semantic_translation"),
                    "final_tts_text": chain.get("final") or chain.get("final_tts_text"),
                },
                "change_reasons": fail.get("change_reasons") or [],
                "validation_metrics": fail.get("validation_metrics") or {},
                "removed_words": (
                    wd.get("baseline_to_final") or wd.get("original_to_semantic") or {}
                ).get("removed_words", []),
                "added_words": (
                    wd.get("baseline_to_final") or wd.get("original_to_semantic") or {}
                ).get("added_words", []),
                "replaced": (
                    wd.get("baseline_to_final") or wd.get("original_to_semantic") or {}
                ).get("replaced", []),
                "raw_mt_divergence": (fail.get("chain_details") or {}).get(
                    "raw_mt_divergence"
                ),
                "raw_mt_word_diff": wd.get("raw_mt_to_semantic") or {},
                "lost_entities": entity_reports,
                "errors": fail.get("errors") or [],
            }
        )

    first = failures[0] if failures else {}
    return {
        "error_type": "SemanticValidationError",
        "primary_reason": first.get("reason") or "entity_loss",
        "problem_segment_count": len(failures),
        "problem_segment_indices": payload.get("problem_segment_indices") or [],
        "segments": segments_diff,
        "entity_loss_reports": all_entity_reports,
        "summary": {
            "total_lost_entities": len(all_entity_reports),
            "lost_entity_names": [
                r.get("lost_entity") for r in all_entity_reports[:20]
            ],
        },
    }


def build_semantic_report_json(
    payload: dict[str, Any],
    *,
    task_id: str = "",
    stage: str = "semantic_validation",
) -> dict[str, Any]:
    """report.json body extension — transformation chains per failed segment (TZ §6)."""
    segments_report: list[dict[str, Any]] = []
    for fail in payload.get("failures") or []:
        chain = fail.get("transformation_chain") or {}
        segments_report.append(
            {
                "index": fail.get("index"),
                "reason": fail.get("reason"),
                "chain": {
                    "original_english": chain.get("original")
                    or chain.get("original_english"),
                    "raw_mt": chain.get("raw_mt"),
                    "semantic_translation": chain.get("semantic")
                    or chain.get("semantic_translation"),
                    "final_tts_text": chain.get("final") or chain.get("final_tts_text"),
                },
                "change_reasons": fail.get("change_reasons") or [],
                "validation_metrics": fail.get("validation_metrics") or {},
                "entity_loss_reports": fail.get("entity_loss_reports") or [],
                "errors": fail.get("errors") or [],
                "quality_analysis": fail.get("quality_analysis") or {},
            }
        )
    runtime = payload.get("runtime_pipeline") or {}
    if not runtime and payload.get("pipeline_stages"):
        runtime = build_runtime_pipeline(
            {"pipeline_stages": payload.get("pipeline_stages")},
            validation_payload=payload,
        )
    stage_summary = summarize_runtime_pipeline(runtime) if runtime else []
    return {
        "stage": stage,
        "task_id": task_id,
        "error_type": "SemanticValidationError",
        "failed_segments": segments_report,
        "problem_segment_indices": payload.get("problem_segment_indices") or [],
        "runtime_pipeline_summary": stage_summary,
        "runtime_pipeline_text": (
            format_runtime_pipeline_block(runtime) if runtime else ""
        ),
    }
