"""Developer Translation Diagnostics — full pipeline trace (dev-only)."""

from __future__ import annotations

import difflib
import os
import re
from typing import Any

from engines.translation_quality import segment_quality_warnings

__all__ = [
    "build_developer_diagnostics",
    "export_diagnostics_text",
    "dev_diagnostics_enabled",
]

_STAGE_DEFS = [
    ("original", "Original"),
    ("lang_detect", "Language Detection"),
    ("split", "Sentence Split"),
    ("ner", "NER"),
    ("entity_manager", "Entity Manager"),
    ("serialization", "Placeholder Serialization"),
    ("translation_manager", "Translation Manager"),
    ("mt_argos", "Argos Result"),
    ("mt_deep", "Deep Result"),
    ("mt_marian", "Marian Result"),
    ("mt_google", "Google Result"),
    ("mt_openai", "OpenAI Result"),
    ("mt_gemini", "Gemini Result"),
    ("tournament", "Tournament"),
    ("fusion", "Fusion"),
    ("natural", "Natural Translation"),
    ("restore", "Entity Restore"),
    ("contract", "Contract Validation"),
    ("timing", "Timing Preparation"),
    ("final", "Final Translation"),
]

_SUMMARY_STAGES = [
    ("lang_detect", "Language Detect"),
    ("split", "Split"),
    ("ner", "NER"),
    ("entity_manager", "Entity"),
    ("translation_manager", "Translation"),
    ("tournament", "Tournament"),
    ("fusion", "Fusion"),
    ("restore", "Restore"),
    ("natural", "Natural"),
    ("contract", "Contract"),
    ("final", "Final"),
]


def dev_diagnostics_enabled() -> bool:
    return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on") or (
        os.getenv("VM_ARCHITECT_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    )


def _esc(s: str) -> str:
    return str(s or "").replace("\r", "")


def _text_diff(before: str, after: str) -> list[dict[str, str]]:
    """Word-level diff chunks for UI."""
    a = _esc(before).split()
    b = _esc(after).split()
    sm = difflib.SequenceMatcher(None, a, b)
    out: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        chunk = " ".join(b[j1:j2]) if tag != "delete" else " ".join(a[i1:i2])
        if not chunk:
            continue
        out.append({"tag": tag, "text": chunk})
    return out


def _engine_stage_id(engine_id: str) -> str:
    e = (engine_id or "").lower()
    for key in ("argos", "deep", "marian", "google", "openai", "gemini", "nllb", "libre"):
        if key in e:
            return f"mt_{key if key != 'libre' else 'google'}"
    return f"mt_{e.replace('-', '_')[:24]}"


def _infer_cause(stage_id: str, issues: list[str], audit: dict[str, Any]) -> str:
    text = " ".join(issues).lower()
    engine = str(audit.get("engine") or "")
    if "placeholder" in text or "leak" in text:
        if engine:
            return f"{engine} изменил или повредил placeholder."
        return "Переводчик изменил placeholder."
    if stage_id == "restore" and audit.get("nat_restored_entities"):
        return "Частичное восстановление сущностей Naturalizer."
    if stage_id == "natural":
        if "proper_noun" in text or "preserved_token" in text:
            return "LLM удалил или изменил имя собственное."
        if "mixed" in text:
            return "Смешение языков после Naturalizer."
    if stage_id == "contract":
        return "Не удалось восстановить все placeholder."
    if issues:
        return "; ".join(issues[:3])
    return ""


def _stage_row(
    stage_id: str,
    name: str,
    *,
    text_in: str = "",
    text_out: str = "",
    ms: float = 0.0,
    engine: str = "",
    score: float | None = None,
    reason: str = "",
    ok: bool = True,
    issues: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues = list(issues or [])
    changed = _esc(text_in).strip() != _esc(text_out).strip()
    row: dict[str, Any] = {
        "id": stage_id,
        "name": name,
        "ok": ok and not issues,
        "input": _esc(text_in),
        "output": _esc(text_out),
        "ms": round(float(ms or 0), 1),
        "engine": engine or "",
        "score": score,
        "reason": reason or "",
        "changed": changed,
        "issues": issues,
        "diff": _text_diff(text_in, text_out) if changed else [],
    }
    if not row["ok"]:
        row["error"] = {
            "stage": name,
            "input": row["input"][:500],
            "output": row["output"][:500],
            "reason": "; ".join(issues) if issues else reason,
            "probable_cause": _infer_cause(stage_id, issues, extra or {}),
        }
    return row


def _build_segment_stages(
    audit: dict[str, Any],
    *,
    task_info: dict[str, Any],
    index: int,
) -> list[dict[str, Any]]:
    original = str(audit.get("whisper_text") or "")
    raw = str(audit.get("raw_translation") or "")
    naturalized = str(audit.get("naturalized_text") or "")
    final = str(audit.get("final_text") or audit.get("tts_text") or "")
    qd = audit.get("quality_details") or {}
    ph = qd.get("pipeline_health") or {}
    architect = audit.get("architect") or {}
    stages: list[dict[str, Any]] = []

    src_lang = task_info.get("detected_lang") or task_info.get("source_lang") or audit.get("source_lang")
    tgt_lang = task_info.get("target_lang") or audit.get("target_lang")

    stages.append(
        _stage_row(
            "original",
            "Original",
            text_in=original,
            text_out=original,
            extra=audit,
        )
    )

    stages.append(
        _stage_row(
            "lang_detect",
            "Language Detection",
            text_in=original[:120],
            text_out=str(src_lang or ""),
            reason=f"target={tgt_lang}",
            extra=audit,
        )
    )

    split_meta = qd.get("split") or {}
    parts = split_meta.get("parts") or []
    split_in = original
    split_out = " | ".join(parts) if parts else original
    stages.append(
        _stage_row(
            "split",
            "Sentence Split",
            text_in=split_in,
            text_out=split_out,
            ok=True,
            extra=audit,
        )
    )

    registry = architect.get("registry") or []
    ner_out = ", ".join(
        f"{r.get('entity_type', '?')}:{r.get('original', '')}" for r in registry[:12]
    ) or "(catalog / proper nouns)"
    stages.append(
        _stage_row(
            "ner",
            "NER",
            text_in=original,
            text_out=ner_out,
            extra=audit,
        )
    )

    masked = architect.get("masked") or ""
    em_out = masked or split_out
    stages.append(
        _stage_row(
            "entity_manager",
            "Entity Manager",
            text_in=original,
            text_out=em_out,
            extra=audit,
        )
    )

    ser_out = em_out
    if registry:
        ser_out = ", ".join(
            f"⟦{r.get('entity_id', '?')}⟧" for r in registry[:8]
        ) or em_out
    stages.append(
        _stage_row(
            "serialization",
            "Placeholder Serialization",
            text_in=em_out,
            text_out=ser_out,
            extra=audit,
        )
    )

    tm_reason = str(audit.get("router_reason") or audit.get("route_label") or "")
    stages.append(
        _stage_row(
            "translation_manager",
            "Translation Manager",
            text_in=ser_out or original,
            text_out=raw,
            ms=float(audit.get("duration_ms") or 0),
            engine=str(audit.get("engine") or ""),
            score=float(audit.get("quality_score") or 0) or None,
            reason=tm_reason,
            extra=audit,
        )
    )

    tournament_scores = audit.get("tournament_scores") or {}
    candidates = architect.get("candidates") or []
    cand_by_engine = {c.get("engine"): c for c in candidates if c.get("engine")}

    for eng, sc in tournament_scores.items():
        sid = _engine_stage_id(eng)
        cand = cand_by_engine.get(eng, {})
        stages.append(
            _stage_row(
                sid,
                f"{eng.title()} Result",
                text_in=ser_out or original,
                text_out=str(cand.get("text") or ""),
                ms=float(cand.get("elapsed_ms") or 0),
                engine=eng,
                score=float(sc) if sc is not None else None,
                ok=bool(cand.get("placeholder_ok", True)),
                issues=[] if cand.get("placeholder_ok", True) else ["placeholder_damaged"],
                extra=audit,
            )
        )

    if not tournament_scores and audit.get("alternative_engine"):
        alt_eng = str(audit.get("alternative_engine"))
        stages.append(
            _stage_row(
                _engine_stage_id(alt_eng),
                f"{alt_eng.title()} (alternative)",
                text_in=original,
                text_out=str(audit.get("alternative_translation") or ""),
                engine=alt_eng,
                score=float(audit.get("alternative_score") or 0) or None,
                extra=audit,
            )
        )

    winner = str(audit.get("engine") or "")
    tourn_reason = ", ".join(
        f"{k}={v}" for k, v in (tournament_scores or {}).items()
    )
    stages.append(
        _stage_row(
            "tournament",
            "Tournament",
            text_in=original,
            text_out=winner,
            reason=tourn_reason or audit.get("routes_tried", []).__str__(),
            extra=audit,
        )
    )

    fusion_issues: list[str] = []
    if ph.get("issues"):
        fusion_issues.extend(ph.get("issues") or [])
    stages.append(
        _stage_row(
            "fusion",
            "Fusion",
            text_in=raw,
            text_out=raw,
            ms=float(audit.get("duration_ms") or 0),
            engine=winner,
            score=float(audit.get("quality_score") or 0) or None,
            reason=str(audit.get("fusion_reason") or tm_reason),
            ok=ph.get("ok", True) if isinstance(ph, dict) else True,
            issues=fusion_issues,
            extra=audit,
        )
    )

    nat_issues = list(audit.get("naturalizer_reasons") or [])
    for w in audit.get("validation_warnings") or []:
        if isinstance(w, dict):
            nat_issues.append(f"{w.get('stage', '')}:{w.get('code', '')}".strip(":"))
    stages.append(
        _stage_row(
            "natural",
            "Natural Translation",
            text_in=raw,
            text_out=naturalized,
            ms=float(audit.get("naturalizer_ms") or 0),
            score=float(audit.get("nat_quality_score") or 0) or None,
            issues=nat_issues,
            extra=audit,
        )
    )

    restore_issues: list[str] = []
    post_stages = (ph.get("stages") or []) if isinstance(ph, dict) else []
    for ps in post_stages:
        if ps.get("stage") == "post_mt_restore" and not ps.get("ok", True):
            restore_issues.extend(ps.get("issues") or [])
    if audit.get("nat_restored_entities"):
        restore_issues.append(f"restored:{len(audit['nat_restored_entities'])}")
    stages.append(
        _stage_row(
            "restore",
            "Entity Restore",
            text_in=raw,
            text_out=naturalized or raw,
            issues=restore_issues,
            extra=audit,
        )
    )

    contract_issues: list[str] = []
    for ps in post_stages:
        if ps.get("stage") == "final_gate" and not ps.get("ok", True):
            contract_issues.extend(ps.get("issues") or [])
    leak = int(qd.get("placeholder_leak_count") or 0)
    if leak:
        contract_issues.append(f"placeholder_leaks:{leak}")
    stages.append(
        _stage_row(
            "contract",
            "Contract Validation",
            text_in=naturalized,
            text_out=final,
            ms=float(audit.get("quality_pass_ms") or 0),
            issues=contract_issues,
            extra=audit,
        )
    )

    stages.append(
        _stage_row(
            "timing",
            "Timing Preparation",
            text_in=final,
            text_out=final,
            ms=float(audit.get("semantic_ms") or 0),
            reason="semantic_adapted" if audit.get("semantic_adapted") else "",
            extra=audit,
        )
    )

    stages.append(
        _stage_row(
            "final",
            "Final Translation",
            text_in=original,
            text_out=final,
            ms=float(audit.get("duration_ms") or 0) + float(audit.get("naturalizer_ms") or 0),
            score=float(audit.get("quality_score") or 0) or None,
            extra=audit,
        )
    )

    return stages


def _pipeline_summary(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Top-level status for quick scan."""
    stage_ok: dict[str, bool] = {sid: True for sid, _ in _SUMMARY_STAGES}
    stop_stage = ""
    stop_reason = ""
    probable = ""

    for seg in segments:
        for st in seg.get("stages") or []:
            sid = st.get("id", "")
            summary_key = sid
            if sid.startswith("mt_"):
                summary_key = "translation_manager"
            if summary_key in stage_ok and not st.get("ok", True):
                stage_ok[summary_key] = False
                if not stop_stage:
                    stop_stage = sid
                    err = st.get("error") or {}
                    stop_reason = err.get("reason") or "; ".join(st.get("issues") or [])
                    probable = err.get("probable_cause") or ""

    rows = []
    for sid, label in _SUMMARY_STAGES:
        ok = stage_ok.get(sid, True)
        rows.append(
            {
                "id": sid,
                "label": label,
                "ok": ok,
                "icon": "✔" if ok else "❌",
            }
        )

    all_ok = all(r["ok"] for r in rows)
    return {
        "ok": all_ok,
        "stages": rows,
        "stopped": not all_ok,
        "stop_stage": stop_stage,
        "stop_reason": stop_reason,
        "probable_cause": probable,
    }


def build_developer_diagnostics(task_info: dict[str, Any]) -> dict[str, Any]:
    """Build full diagnostics payload from task info + audits."""
    if not dev_diagnostics_enabled():
        return {"enabled": False, "segments": [], "summary": {}}

    audits = task_info.get("translation_audits") or []
    source_segments = task_info.get("source_segments") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}

    segments_out: list[dict[str, Any]] = []
    for i, src in enumerate(source_segments):
        audit = audit_by_idx.get(i, {})
        if not audit:
            audit = {"index": i, "whisper_text": str(src or "")}
        stages = _build_segment_stages(audit, task_info=task_info, index=i)
        warnings = segment_quality_warnings(
            original=str(audit.get("whisper_text") or src or ""),
            raw=str(audit.get("raw_translation") or ""),
            naturalized=str(audit.get("naturalized_text") or ""),
            final=str(audit.get("final_text") or ""),
            tts_text=str(audit.get("tts_text") or audit.get("final_text") or ""),
            source_lang=task_info.get("detected_lang") or task_info.get("source_lang"),
            target_lang=task_info.get("target_lang"),
        )
        tournament = {
            "scores": audit.get("tournament_scores") or {},
            "winner": audit.get("engine") or "",
            "fusion_reason": audit.get("fusion_reason") or audit.get("router_reason") or "",
            "engines": audit.get("tournament_engines") or audit.get("routes_tried") or [],
        }
        segments_out.append(
            {
                "index": i + 1,
                "original": str(audit.get("whisper_text") or src or ""),
                "final": str(audit.get("final_text") or audit.get("tts_text") or ""),
                "stages": stages,
                "tournament": tournament,
                "warnings": warnings,
                "pipeline_health": (audit.get("quality_details") or {}).get("pipeline_health"),
            }
        )

    summary = _pipeline_summary(segments_out)
    return {
        "enabled": True,
        "version": 1,
        "task_id": task_info.get("task_id") or "",
        "source_lang": task_info.get("detected_lang") or task_info.get("source_lang"),
        "target_lang": task_info.get("target_lang"),
        "segment_count": len(segments_out),
        "summary": summary,
        "segments": segments_out,
        "trace_log": task_info.get("translation_trace_log"),
        "dev_logs": task_info.get("dev_diagnostics"),
    }


def export_diagnostics_text(diag: dict[str, Any]) -> str:
    """Single text file for ChatGPT / manual analysis."""
    lines = [
        "TubeDub — Developer Translation Diagnostics",
        f"Source: {diag.get('source_lang')} → Target: {diag.get('target_lang')}",
        f"Segments: {diag.get('segment_count', 0)}",
        "",
    ]
    summary = diag.get("summary") or {}
    if summary.get("stopped"):
        lines.extend(
            [
                "Pipeline stopped",
                f"Stage: {summary.get('stop_stage', '')}",
                f"Reason: {summary.get('stop_reason', '')}",
                f"Probable cause: {summary.get('probable_cause', '')}",
                "",
            ]
        )
    lines.append("Pipeline Status:")
    for row in summary.get("stages") or []:
        lines.append(f"  {row.get('icon', '?')} {row.get('label', '')}")
    lines.append("")

    for seg in diag.get("segments") or []:
        lines.append(f"{'=' * 60}")
        lines.append(f"Segment #{seg.get('index', '?')}")
        lines.append(f"Original: {seg.get('original', '')}")
        lines.append(f"Final:    {seg.get('final', '')}")
        tourn = seg.get("tournament") or {}
        if tourn.get("scores"):
            lines.append("Tournament scores:")
            for eng, sc in tourn["scores"].items():
                mark = " ← Winner" if eng == tourn.get("winner") else ""
                lines.append(f"  {eng}: {sc}{mark}")
            if tourn.get("fusion_reason"):
                lines.append(f"Fusion: {tourn['fusion_reason']}")
        lines.append("")
        for st in seg.get("stages") or []:
            mark = "❌" if not st.get("ok", True) else "✔"
            lines.append(f"{mark} {st.get('name', st.get('id', ''))}")
            if st.get("ms"):
                lines.append(f"  Time: {st.get('ms')} ms")
            if st.get("engine"):
                lines.append(f"  Engine: {st.get('engine')}")
            if st.get("score") is not None:
                lines.append(f"  Score: {st.get('score')}")
            if st.get("reason"):
                lines.append(f"  Reason: {st.get('reason')}")
            if st.get("changed"):
                lines.append(f"  Input:  {st.get('input', '')[:800]}")
                lines.append(f"  Output: {st.get('output', '')[:800]}")
            err = st.get("error") or {}
            if err:
                lines.append(f"  Error: {err.get('reason', '')}")
                if err.get("probable_cause"):
                    lines.append(f"  Probable: {err.get('probable_cause')}")
            lines.append("")
        lines.append("")

    if diag.get("trace_log"):
        lines.append(f"Trace log: {diag.get('trace_log')}")
    return "\n".join(lines)
