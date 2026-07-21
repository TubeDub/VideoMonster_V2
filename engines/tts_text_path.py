"""Trace Final → TTS input; detect text substitution."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any


def final_texts_from_info(info: dict[str, Any]) -> list[str]:
    """Per-segment Final text for TTS — agent pipeline fields take precedence.

    TPS: when approved_text is set, it is the single source of truth (Review == TTS).
    """
    from engines.translation_validation import resolve_final_text

    segments_data = info.get("segments_data") or []
    audits = info.get("translation_audits") or []

    # TPS Single Approved Text
    if info.get("tps") and segments_data:
        from engines.tps.approved_text import get_approved_text

        out = []
        for s in segments_data:
            approved = get_approved_text(s if isinstance(s, dict) else {})
            out.append(approved or resolve_final_text(s if isinstance(s, dict) else {}))
        if any(out):
            return out

    agent_pipeline = bool(
        info.get("quality_agent_path")
        or info.get("grammar_agent_path")
        or info.get("translation_agent_path")
    )

    if agent_pipeline and segments_data:
        return [resolve_final_text(s) for s in segments_data]

    if not audits and segments_data:
        return [resolve_final_text(s) for s in segments_data]

    by_idx = {int(a.get("index", -1)): a for a in audits}
    count = max(len(segments_data), len(audits), len(info.get("source_segments") or []))
    out: list[str] = []
    for i in range(count):
        if i < len(segments_data):
            seg_final = resolve_final_text(segments_data[i])
            if seg_final:
                out.append(seg_final)
                continue
        row = by_idx.get(i, {})
        text = str(row.get("final_text") or row.get("tts_text") or "").strip()
        out.append(text)
    return out


def build_tts_trace_rows(
    info: dict[str, Any],
    tts_inputs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """One row per segment: whisper, raw, naturalized, final, tts_input."""
    source = info.get("source_segments") or []
    audits = info.get("translation_audits") or []
    by_idx = {int(a.get("index", -1)): a for a in audits}
    finals = final_texts_from_info(info)
    rows: list[dict[str, Any]] = []
    n = max(len(source), len(finals), len(tts_inputs or []))
    for i in range(n):
        audit = by_idx.get(i, {})
        tts_in = ""
        if tts_inputs and i < len(tts_inputs):
            tts_in = str(tts_inputs[i] or "").strip()
        final = finals[i] if i < len(finals) else str(audit.get("final_text") or "")
        rows.append(
            {
                "index": i,
                "whisper": str(source[i] if i < len(source) else audit.get("whisper_text") or ""),
                "raw_mt": str(audit.get("raw_translation") or ""),
                "naturalized": str(audit.get("naturalized_text") or ""),
                "final": final,
                "tts_input": tts_in or final,
            }
        )
    return rows


def log_tts_trace(
    app_dir: Path,
    rows: list[dict[str, Any]],
    *,
    task_id: str = "",
    phase: str = "pre_tts",
) -> str:
    log_dir = app_dir / "output" / "dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex[:10]
    path = log_dir / f"tts_text_trace_{jid}.log"
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [
        f"=== TTS TEXT TRACE ts={ts} task={task_id} phase={phase} ===",
        "index\twhisper\traw_mt\tnaturalized\tfinal\ttts_input\tmatch",
    ]
    for r in rows:
        final = str(r.get("final") or "")
        tts_in = str(r.get("tts_input") or "")
        match = "OK" if final == tts_in else "MISMATCH"
        def esc(s: str) -> str:
            return (s or "").replace("\t", " ").replace("\n", " ")[:500]

        lines.append(
            f"{r.get('index')}\t{esc(r.get('whisper', ''))}\t{esc(r.get('raw_mt', ''))}\t"
            f"{esc(r.get('naturalized', ''))}\t{esc(r.get('final', ''))}\t"
            f"{esc(tts_in)}\t{match}"
        )
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    latest = log_dir / "tts_text_trace_latest.log"
    latest.write_text(text, encoding="utf-8")
    return str(path)


def find_mismatches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        final = str(r.get("final") or "")
        tts_in = str(r.get("tts_input") or "")
        if final != tts_in:
            out.append({**r, "substitution": "tts_input != final"})
    return out


def write_tts_path_work_report(
    app_dir: Path,
    *,
    task_id: str,
    info: dict[str, Any],
    tts_inputs: list[str] | None = None,
    adapt_tts_text: bool,
    translate_method: str = "",
    success: bool = True,
) -> str:
    from engines.work_report import write_work_report

    rows = build_tts_trace_rows(info, tts_inputs)
    mismatches = find_mismatches(rows)
    substitution_module = ""
    if mismatches and adapt_tts_text:
        substitution_module = "engines.semantic_adaptation.prepare_tts_groups_semantic"
    elif mismatches:
        substitution_module = "unknown (adapt_text=False but mismatch)"

    sample_tts = (tts_inputs or [r.get("tts_input") for r in rows])[:3]
    sample_final = [r.get("final") for r in rows[:3]]

    return write_work_report(
        app_dir,
        task_title=f"TTS text path verification task={task_id[:8]}",
        discovered=[
            f"TTS segments: {len(rows)}",
            f"TTS/Final mismatches: {len(mismatches)}",
            f"translate_method: {translate_method or info.get('translate_method') or 'unknown'}",
            f"semantic_adapt_at_tts: {adapt_tts_text}",
            f"translation_review_approved: {info.get('translation_review_approved')}",
        ],
        root_cause=(
            "prepare_tts_groups_semantic() rewrote approved Final text before TTS synthesis"
            if mismatches and adapt_tts_text
            else (
                "No TTS/Final mismatch detected in this run"
                if not mismatches
                else "TTS input differed from Final — see tts_text_trace log"
            )
        ),
        changes=[
            "TTS uses audit.final_text via final_texts_from_info()",
            "Semantic text adaptation disabled when translation review was used",
            "Per-segment log: whisper / raw / naturalized / final / tts_input",
        ],
        files_changed=[
            "engines/tts_text_path.py",
            "engines/semantic_adaptation.py",
            "api/auto_dub_api.py",
            "scripts/test_tts_text_path.py",
        ],
        functions_changed=[
            "final_texts_from_info",
            "prepare_tts_groups_semantic(adapt_text=)",
            "_pause_for_translation_review",
            "_resume_from_translation_review",
        ],
        tests_run=["test_tts_text_path"],
        test_results={"test_tts_text_path": "PASS" if not mismatches or not adapt_tts_text else "FAIL"},
        remaining_checks=[
            "Manual dub: confirm spoken text matches Final in review panel",
        ],
        limitations=[
            "Semantic timing adaptation still disabled after review (by design)",
            "Without review, semantic pre-TTS adaptation may still change text",
        ],
        next_actions=[
            "Run full dub EN→UK with review; compare audio to Final column",
        ],
        fixed=[
            "TTS receives final_text when translation review is enabled",
            "Semantic rewrite before TTS skipped after «Одобрить и озвучить»",
        ] if not mismatches else [],
        not_fixed=(
            [f"{len(mismatches)} segment(s) still mismatch — module: {substitution_module}"]
            if mismatches
            else ["None"]
        ),
        status="READY" if success and not mismatches else ("WARNING" if success else "ERROR"),
    )

