"""
OpenDDF Analyzer 2.0 — enrich OpenDDF JSON for the developer diagnostic UI.
Does not modify pipeline; only reads task_info / report payloads.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ANALYZER_VERSION = "2.0.0"

_PIPELINE_STAGE_DEFS = (
    ("original", "Original", "stt"),
    ("raw_mt", "Raw MT", "translation_manager"),
    ("semantic", "Semantic Translation", "translation_naturalizer"),
    ("timing_aware", "Timing-Aware Adaptation", "timing_aware_translation"),
    ("adaptation", "Text Adaptation", "semantic_optimizer"),
    ("meaning_validation", "Meaning Validation", "semantic_meaning"),
    ("final_text", "Final Text", "translation_review"),
    ("tts_input", "TTS Input", "tts"),
    ("tts_audio", "Generated Audio", "tts"),
    ("slot_fit", "Slot Fit", "slot_fit"),
    ("final_mix", "Final Mix", "studio"),
)


def _overflow_pct(slot_ms: int, playback_ms: int) -> float:
    if slot_ms <= 0:
        return 0.0
    return round(max(0.0, (playback_ms - slot_ms) / slot_ms * 100.0), 1)


def _overflow_band(pct: float) -> str:
    if pct <= 5.0:
        return "green"
    if pct <= 15.0:
        return "yellow"
    return "red"


def _text_diff(before: str, after: str) -> dict[str, Any]:
    from engines.smart_segment_optimizer.diff import compute_text_diff

    if before == after:
        return {"removed_words": [], "added_words": [], "replaced_words": [], "reordered": False}
    return compute_text_diff(before or "", after or "")


def _parse_log_lines(task_id: str, app_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    log_path = app_dir / "output" / "logs" / "tubedub.log"
    if not log_path.is_file():
        return rows
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    pat = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] ([^:]+): (.*)$"
    )
    for line in content.splitlines():
        if task_id and task_id not in line:
            continue
        m = pat.match(line.strip())
        if not m:
            continue
        level = m.group(2).upper()
        if level == "CRITICAL":
            level = "CRITICAL"
        module = m.group(3).strip()
        msg = m.group(4).strip()
        seg_m = re.search(r"(?:segment|idx|Seg)\s*[=:]?\s*(\d+)", msg, re.I)
        rows.append(
            {
                "time": m.group(1),
                "level": level,
                "module": module,
                "message": msg,
                "segment_index": int(seg_m.group(1)) if seg_m else None,
            }
        )
    return rows[-2000:]


def _timeline_from_task(task_info: dict[str, Any]) -> list[dict[str, Any]]:
    stages = task_info.get("pipeline_stages") or {}
    runtime = task_info.get("runtime_pipeline_summary") or {}
    passive = task_info.get("passive_openddf") or {}
    timeline: list[dict[str, Any]] = []

    order = [
        ("stt", "STT"),
        ("translation", "Translation"),
        ("semantic", "Semantic"),
        ("adaptation", "Adaptation"),
        ("tts", "TTS"),
        ("slot_fit", "Slot Fit"),
        ("mix", "Mix"),
        ("mp4", "MP4"),
    ]
    for key, label in order:
        row = stages.get(key) or {}
        dur = row.get("duration_ms") or row.get("elapsed_ms")
        if dur is None and isinstance(runtime, dict):
            dur = runtime.get(f"{key}_ms") or runtime.get(key)
        timeline.append(
            {
                "id": key,
                "label": label,
                "duration_ms": int(dur or 0),
                "status": row.get("status") or ("OK" if dur else "unknown"),
            }
        )
    for ev in passive.get("timeline") or []:
        if isinstance(ev, dict):
            timeline.append(
                {
                    "id": ev.get("event") or "event",
                    "label": str(ev.get("event") or "event"),
                    "duration_ms": 0,
                    "status": ev.get("status") or "OK",
                    "source": "passive_openddf",
                }
            )
    return timeline


def _build_adaptation_attempts(seg: dict[str, Any], audit_qd: dict[str, Any]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    timing_aware = audit_qd.get("timing_aware") or {}
    for i, stage in enumerate(seg.get("adaptation_stages") or timing_aware.get("optimization_stages") or []):
        attempts.append(
            {
                "attempt": i + 1,
                "algorithm": stage.get("stage") or stage.get("name") or "rule",
                "status": stage.get("status") or "applied",
                "reason": stage.get("reason") or "",
                "rejected_reason": stage.get("rejected_reason") or "",
            }
        )
    post = seg.get("optimization_retries") or {}
    for reason in post.get("reasons") or []:
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "algorithm": "post_tts_retry",
                "status": "rejected" if "failed" in str(reason).lower() else "applied",
                "reason": str(reason),
            }
        )
    if seg.get("requires_llm_adaptation"):
        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "algorithm": "requires_llm_adaptation",
                "status": "pending",
                "reason": "rule-based could not fit slot",
            }
        )
    return attempts


def _build_pipeline_stages(seg: dict[str, Any], audit: dict[str, Any]) -> list[dict[str, Any]]:
    qd = audit.get("quality_details") or {}
    timing_aware = qd.get("timing_aware") or {}
    chain: list[dict[str, Any]] = []

    def _step(
        stage_id: str,
        name: str,
        module: str,
        in_text: str,
        out_text: str,
        *,
        duration_ms: int = 0,
        status: str = "ok",
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        decision_reason: str = "",
    ) -> None:
        chain.append(
            {
                "id": stage_id,
                "name": name,
                "module": module,
                "input_text": in_text,
                "output_text": out_text,
                "diff": _text_diff(in_text, out_text),
                "duration_ms": duration_ms,
                "status": status,
                "errors": list(errors or []),
                "warnings": list(warnings or seg.get("warnings") or []),
                "decision_reason": decision_reason,
            }
        )

    original = str(seg.get("original_text") or "")
    raw_mt = str(seg.get("raw_translation") or "")
    semantic = str(seg.get("semantic_engine_text") or seg.get("translated_text") or "")
    translated = str(seg.get("translated_text") or "")
    pre_tts = str(seg.get("pre_tts_text") or translated)
    after_adapt = str(seg.get("text_after_adaptation") or seg.get("final_tts_text") or pre_tts)
    final_tts = str(seg.get("final_tts_text") or after_adapt)

    _step("original", "Original", "stt", "", original)
    _step("raw_mt", "Raw MT", "translation_manager", original, raw_mt or translated)
    _step("semantic", "Semantic Translation", "translation_naturalizer", raw_mt or original, semantic or translated)
    ta_before = str(timing_aware.get("text_before") or semantic or translated)
    ta_after = str(timing_aware.get("text_after") or pre_tts)
    _step(
        "timing_aware",
        "Timing-Aware Adaptation",
        "timing_aware_translation",
        ta_before,
        ta_after,
        duration_ms=int(timing_aware.get("elapsed_ms") or 0),
        decision_reason=str(timing_aware.get("reason") or ""),
    )
    _step(
        "adaptation",
        "Text Adaptation",
        "semantic_optimizer",
        ta_after,
        after_adapt,
        decision_reason=str(seg.get("algorithm_reason") or ""),
    )
    meaning_ok = seg.get("meaning_loss_score")
    mv_status = "ok" if meaning_ok is None or float(meaning_ok) < 0.35 else "warning"
    _step(
        "meaning_validation",
        "Meaning Validation",
        "semantic_meaning",
        after_adapt,
        after_adapt,
        status=mv_status,
        warnings=list(seg.get("quality_reasons") or []),
    )
    _step("final_text", "Final Text", "translation_review", after_adapt, final_tts)
    _step("tts_input", "TTS Input", "tts", final_tts, final_tts)
    audio_status = "ok" if seg.get("final_tts_duration_ms") else "missing"
    _step(
        "tts_audio",
        "Generated Audio",
        "tts",
        final_tts,
        final_tts,
        duration_ms=int(seg.get("final_tts_duration_ms") or 0),
        status=audio_status,
    )
    slot_reason = str(seg.get("algorithm_reason") or "")
    if seg.get("gap_absorb", {}).get("mode"):
        slot_reason = f"gap_absorb: {seg['gap_absorb']['mode']}"
    if seg.get("block_merge", {}).get("block_merged_with_next"):
        slot_reason = "block_merge: next segment allows borrow"
    _step(
        "slot_fit",
        "Slot Fit",
        "slot_fit",
        final_tts,
        final_tts,
        decision_reason=slot_reason,
    )
    _step("final_mix", "Final Mix", "studio", final_tts, final_tts)

    return chain


def _build_entities(seg: dict[str, Any], target_lang: str) -> list[dict[str, Any]]:
    from engines.dubbing_engine.entities import extract_entities
    from engines.semantic_meaning import check_critical_entities

    original = str(seg.get("original_text") or "")
    translated = str(seg.get("translated_text") or "")
    final = str(seg.get("final_tts_text") or seg.get("text_after_adaptation") or "")
    entities: list[dict[str, Any]] = []

    for ent in extract_entities(original, tgt_lang=target_lang):
        label = ent.label or "ENTITY"
        category = {
            "PERSON": "people",
            "CAR": "cars",
            "BRAND": "companies",
            "ORG": "organizations",
            "GEO": "cities",
        }.get(label, label.lower())
        in_trans = ent.text.lower() in translated.lower() or ent.translation.lower() in translated.lower()
        in_final = ent.text.lower() in final.lower() or ent.translation.lower() in final.lower()
        critical_errors = check_critical_entities(original, final)
        bad = any(e.get("value") == ent.text for e in critical_errors)
        entities.append(
            {
                "value": ent.text,
                "category": category,
                "expected": ent.translation,
                "original": ent.text,
                "translation": translated if in_trans else "(missing)",
                "final": final if in_final else "(missing)",
                "critical": bad,
            }
        )

    for m in re.findall(r"\b\d{4}\b", original):
        entities.append(
            {
                "value": m,
                "category": "dates",
                "original": m,
                "translation": m if m in translated else "(missing)",
                "final": m if m in final else "(missing)",
                "critical": m not in final,
            }
        )
    for m in re.findall(r"\b\d+(?:[.,]\d+)?\b", original):
        if len(m) == 4 and m.startswith(("19", "20")):
            continue
        entities.append(
            {
                "value": m,
                "category": "numbers",
                "original": m,
                "translation": m if m in translated else "(missing)",
                "final": m if m in final else "(missing)",
                "critical": m not in final,
            }
        )

    return entities


def _integrity_checks(seg: dict[str, Any], target_lang: str) -> list[dict[str, Any]]:
    from engines.pipeline_language_gate import is_critical_language_mismatch

    final = str(seg.get("final_tts_text") or "")
    original = str(seg.get("original_text") or "")
    translated = str(seg.get("translated_text") or "")
    slot_ms = int(seg.get("slot_ms") or seg.get("original_duration_ms") or 0)
    playback = int(seg.get("final_tts_duration_ms") or seg.get("actual_duration_ms") or 0)
    overflow = _overflow_pct(slot_ms, playback)

    checks: list[dict[str, Any]] = []

    def _add(code: str, ok: bool, message: str) -> None:
        checks.append({"code": code, "ok": ok, "message": message})

    bad_lang, lang_code = is_critical_language_mismatch(
        final, target_lang=target_lang, original=original
    )
    _add("target_language", not bad_lang, lang_code or "language OK")
    _add("no_english_leak", not bad_lang, "no English in target track" if not bad_lang else lang_code)
    entity_crit = any(e.get("critical") for e in seg.get("entities") or [])
    _add("entity_preservation", not entity_crit, "entities preserved" if not entity_crit else "entity loss")
    ml = seg.get("meaning_loss_score")
    _add(
        "meaning_preserved",
        ml is None or float(ml) < 0.4,
        f"meaning_loss_score={ml}",
    )
    _add("overflow_tolerance", overflow <= 15.0, f"overflow {overflow}%")
    _add("no_overlap", not seg.get("overlap_info", {}).get("slot_overflow"), "no slot overflow flag")
    _add(
        "tts_text_sync",
        final.strip() == str(seg.get("pre_tts_text") or final).strip() or bool(seg.get("adaptation_executed")),
        "TTS matches final text chain",
    )
    _add(
        "not_source_leak",
        final.strip().lower() != original.strip().lower() or not original,
        "final is translation not raw source",
    )
    return checks


def _audio_block(seg: dict[str, Any], task_info: dict[str, Any], idx: int) -> dict[str, Any]:
    segments_data = task_info.get("segments_data") or []
    raw_seg = segments_data[idx] if idx < len(segments_data) else {}
    out_dir = str(task_info.get("output_dir") or "output")

    def _meta(path: str | None) -> dict[str, Any]:
        if not path:
            return {}
        p = Path(path)
        if not p.is_file():
            p = Path(out_dir) / path
        if not p.is_file():
            return {"path": str(path), "exists": False}
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return {
            "path": str(p.resolve()),
            "filename": p.name,
            "exists": True,
            "size_bytes": size,
            "duration_ms": seg.get("final_tts_duration_ms"),
        }

    return {
        "original": _meta(task_info.get("original_audio_path") or task_info.get("audio_path")),
        "tts": _meta(raw_seg.get("file") or raw_seg.get("tts_file_path")),
        "fitted": _meta(raw_seg.get("fitted_file")),
        "final": _meta(raw_seg.get("fitted_file") or raw_seg.get("file")),
    }


def _enrich_segment(
    seg: dict[str, Any],
    audit: dict[str, Any],
    task_info: dict[str, Any],
    target_lang: str,
) -> dict[str, Any]:
    qd = audit.get("quality_details") or {}
    slot_ms = int(seg.get("slot_ms") or seg.get("original_duration_ms") or 0)
    playback = int(seg.get("final_tts_duration_ms") or seg.get("actual_duration_ms") or 0)
    overflow = _overflow_pct(slot_ms, playback)
    enriched = dict(seg)
    enriched["pipeline_stages"] = _build_pipeline_stages(enriched, audit)
    enriched["adaptation_attempts"] = _build_adaptation_attempts(enriched, qd)
    enriched["timing_detail"] = {
        "slot_duration_ms": slot_ms,
        "tts_duration_ms": int(seg.get("first_tts_duration_ms") or playback),
        "playback_duration_ms": playback,
        "original_duration_ms": int(seg.get("original_duration_ms") or slot_ms),
        "overflow_pct": overflow,
        "overflow_band": _overflow_band(overflow),
        "underflow_ms": max(0, slot_ms - playback),
        "gap_absorb": seg.get("gap_absorb") or {},
        "video_adapt": seg.get("gap_absorb") or {},
        "block_merge": seg.get("block_merge") or {},
        "final_placement_ms": {
            "start": seg.get("start_time_ms"),
            "end": seg.get("end_time_ms"),
        },
    }
    enriched["entities"] = _build_entities(enriched, target_lang)
    enriched["integrity_checks"] = _integrity_checks(enriched, target_lang)
    enriched["audio"] = _audio_block(enriched, task_info, int(seg.get("index", 0)))
    task_id = task_info.get("task_id") or ""
    idx = int(seg.get("index", 0))
    enriched["editor_links"] = {
        "translation": f"/dub?focus_segment={idx}&task_id={task_id}",
        "tts": f"/studio?task_id={task_id}&segment={idx}",
        "timeline": f"/dev/pipeline/{task_id}#segment-{idx}",
    }
    return enriched


def _aggregate_statistics(segments: list[dict[str, Any]]) -> dict[str, Any]:
    overflows = [float(s.get("timing_detail", {}).get("overflow_pct") or 0) for s in segments]
    stats = {
        "segment_count": len(segments),
        "avg_overflow_pct": round(sum(overflows) / max(len(overflows), 1), 1),
        "max_overflow_pct": round(max(overflows) if overflows else 0, 1),
        "gap_absorb_count": sum(1 for s in segments if (s.get("gap_absorb") or {}).get("mode")),
        "block_merge_count": sum(
            1 for s in segments if (s.get("block_merge") or {}).get("block_merged_with_next")
        ),
        "video_adapt_count": sum(
            1 for s in segments if (s.get("gap_absorb") or {}).get("video_stretch_ratio")
        ),
        "llm_adaptation_count": sum(
            1 for s in segments if s.get("requires_llm_adaptation")
        ),
        "rule_adaptation_count": sum(1 for s in segments if s.get("adaptation_executed")),
        "failed_adaptation_count": sum(1 for s in segments if s.get("problematic")),
        "english_leak_count": sum(
            1 for s in segments
            if any(c.get("code") == "no_english_leak" and not c.get("ok") for c in s.get("integrity_checks") or [])
        ),
        "entity_loss_count": sum(
            1 for s in segments if any(e.get("critical") for e in s.get("entities") or [])
        ),
        "meaning_loss_count": sum(
            1 for s in segments
            if any(c.get("code") == "meaning_preserved" and not c.get("ok") for c in s.get("integrity_checks") or [])
        ),
        "timing_error_count": sum(
            1 for s in segments
            if float(s.get("timing_detail", {}).get("overflow_pct") or 0) > 15
        ),
        "semantic_error_count": 0,
        "tts_error_count": sum(
            1 for s in segments if not (s.get("audio") or {}).get("tts", {}).get("exists")
        ),
    }
    return stats


def build_analyzer_v2_report(
    raw: dict[str, Any],
    *,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Build Analyzer 2.0 report from task_info or existing OpenDDF JSON.
    All data derived dynamically — no embedded demo arrays.
    """
    base = Path(app_dir or Path(__file__).resolve().parent.parent)

    task_info: dict[str, Any] = {}
    if raw.get("segments") and raw.get("task_id"):
        report = dict(raw)
        task_info = dict(raw.get("task_info") or {})
        task_info.setdefault("task_id", report.get("task_id"))
        task_info.setdefault("target_lang", report.get("target_lang"))
    elif raw.get("segments_data") or raw.get("translation_audits"):
        task_info = dict(raw)
        from engines.segment_timing_qa import build_openddf_full_report

        report = build_openddf_full_report(task_info)
    else:
        report = dict(raw)
        task_info.setdefault("task_id", report.get("task_id"))

    task_id = str(report.get("task_id") or task_info.get("task_id") or "")
    target_lang = str(report.get("target_lang") or task_info.get("target_lang") or "uk")
    audits = task_info.get("translation_audits") or []
    audit_by = {int(a.get("index", -1)): a for a in audits}

    segments = []
    for seg in report.get("segments") or []:
        idx = int(seg.get("index", 0))
        enriched = _enrich_segment(seg, audit_by.get(idx, {}), task_info, target_lang)
        segments.append(enriched)

    logs = _parse_log_lines(task_id, base)
    if not logs and task_info.get("passive_openddf"):
        for ev in task_info.get("passive_openddf", {}).get("timeline") or []:
            if isinstance(ev, dict):
                logs.append(
                    {
                        "time": ev.get("timestamp") or "",
                        "level": ev.get("status") or "INFO",
                        "module": "passive_openddf",
                        "message": str(ev.get("event") or ""),
                        "segment_index": None,
                    }
                )

    timeline = _timeline_from_task(task_info)
    if not any(t.get("duration_ms") for t in timeline):
        timeline = [
            {"id": s[0], "label": s[1], "duration_ms": 0, "status": "unknown", "module": s[2]}
            for s in _PIPELINE_STAGE_DEFS[:8]
        ]

    statistics = _aggregate_statistics(segments)

    return {
        "analyzer_version": ANALYZER_VERSION,
        "task_id": task_id,
        "target_lang": target_lang,
        "generated_at": report.get("generated_at"),
        "summary": report.get("summary") or {},
        "segments": segments,
        "skipped_segments": report.get("skipped_segments") or [],
        "overlaps": report.get("overlaps") or [],
        "pipeline_timeline": timeline,
        "runtime_logs": logs,
        "statistics": statistics,
        "source_separation": report.get("source_separation") or {},
        "post_tts_qa": report.get("post_tts_qa") or {},
        "final_dub_qa": report.get("final_dub_qa") or {},
        "storage_report": report.get("storage_report") or {},
        "ai_installation": report.get("ai_installation") or {},
        "flags": [f for f in (report.get("flags") or []) if f],
        "task_info_snapshot": {
            "status": task_info.get("pipeline_error"),
            "voice": task_info.get("voice"),
            "session_dir": task_info.get("session_dir"),
        },
    }


def export_analyzer_html(report: dict[str, Any]) -> str:
    """Standalone HTML export for Analyzer report."""
    payload = json.dumps(report, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>OpenDDF Analyzer 2.0 — {report.get('task_id', '')}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#11111b;color:#cdd6f4;padding:24px}}
h1,h2{{color:#fff}} .seg{{border:1px solid #313244;border-radius:8px;padding:12px;margin:12px 0}}
.bad{{color:#f38ba8}} .ok{{color:#a6e3a1}}
</style></head><body>
<h1>OpenDDF Analyzer 2.0</h1>
<p>Task: {report.get('task_id','')} · Segments: {report.get('statistics',{}).get('segment_count',0)}</p>
<pre id="data"></pre>
<script>
const report = {payload};
document.getElementById('data').textContent = JSON.stringify(report, null, 2);
</script></body></html>"""
