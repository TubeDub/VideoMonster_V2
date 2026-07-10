"""Developer Translation Inspector — per-stage pipeline trace with integrity checks."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from engines.placeholder_guard import detect_placeholder_leaks, has_mt_garbage
from engines.translation_quality import segment_quality_warnings

__all__ = [
    "build_translation_inspector",
    "export_inspector_text",
    "export_inspector_json",
    "inspector_enabled",
    "analyze_text_integrity",
]

_PLACEHOLDER_PAT = re.compile(
    r"\[##\s*\d+\s*##\]|\[\s*#+\s*\d+\s*#+\s*\]|⟦[^⟧\n]{0,24}⟧?",
    re.IGNORECASE,
)
_CJK_PAT = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_EN_WORD_PAT = re.compile(r"\b[A-Za-z]{3,}\b")


def inspector_enabled() -> bool:
    return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on") or (
        os.getenv("VM_ARCHITECT_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    )


def _esc(s: str) -> str:
    return str(s or "").replace("\r", "")


def _count_placeholders(text: str) -> int:
    return len(_PLACEHOLDER_PAT.findall(str(text or "")))


def analyze_text_integrity(
    text: str,
    *,
    entities: list[str] | None = None,
    token_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    t = str(text or "")
    issues: list[str] = []
    leaks = detect_placeholder_leaks(t)

    try:
        t.encode("utf-8")
        utf8_ok = True
    except UnicodeEncodeError:
        utf8_ok = False
        issues.append("encoding_mismatch")

    if "\ufffd" in t or "�" in t:
        issues.append("unicode_replacement_char")

    if has_mt_garbage(t):
        issues.append("placeholder_damaged")

    if _CJK_PAT.search(t):
        issues.append("chinese_characters_detected")

    en_words = _EN_WORD_PAT.findall(t)
    if len(en_words) > 2:
        issues.append("english_words_detected")

    ph_count = _count_placeholders(t)
    if leaks and ph_count == 0:
        issues.append("unknown_placeholder")

    expected = len(token_map or {})
    if expected and ph_count == 0 and leaks:
        issues.append("placeholder_lost")

    if entities:
        missing = [e for e in entities if e.lower() not in t.lower() and not any(
            p.lower() in t.lower() for p in e.split() if len(p) > 2
        )]
        if missing and not has_mt_garbage(t):
            issues.append("missing_entity")

    return {
        "char_count": len(t),
        "placeholder_count": ph_count,
        "entity_count": len(entities or []),
        "expected_tokens": len(token_map or {}),
        "utf8_ok": utf8_ok,
        "english_word_count": len(en_words),
        "issues": issues,
        "ok": not issues,
    }


def _compare_integrity(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    stage_from: str,
    stage_to: str,
) -> dict[str, Any]:
    issues: list[str] = []
    if before.get("placeholder_count", 0) > 0 and after.get("placeholder_count", 0) == 0:
        if before.get("issues") and "placeholder_damaged" not in after.get("issues", []):
            pass
    if before.get("placeholder_count", 0) > after.get("placeholder_count", 0):
        if after.get("issues") and any("placeholder" in i for i in after.get("issues", [])):
            issues.append("placeholder_lost")
    if before.get("utf8_ok") and not after.get("utf8_ok"):
        issues.append("encoding_mismatch")
    if not before.get("issues") and after.get("issues"):
        for code in after["issues"]:
            if code not in (before.get("issues") or []):
                issues.append(code)
    new_issues = list(dict.fromkeys(issues + (after.get("issues") or [])))
    return {
        "from": stage_from,
        "to": stage_to,
        "ok": not new_issues,
        "issues": new_issues,
        "regression": bool(issues),
    }


def _stage(
    stage_id: str,
    name: str,
    text: str,
    *,
    ms: float = 0.0,
    meta: dict[str, Any] | None = None,
    entities: list[str] | None = None,
    token_map: dict[str, str] | None = None,
    list_only: bool = False,
) -> dict[str, Any]:
    meta = meta or {}
    integrity = analyze_text_integrity(
        "" if list_only else text,
        entities=entities,
        token_map=token_map,
    )
    row: dict[str, Any] = {
        "id": stage_id,
        "name": name,
        "text": _esc(text),
        "ms": round(float(ms or 0), 1),
        "integrity": integrity,
        "ok": integrity.get("ok", True),
        "issues": list(integrity.get("issues") or []),
        "meta": meta,
    }
    if list_only:
        row["entities"] = list(entities or [])
    return row


def _quality_scores(audit: dict[str, Any], qd: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine_score": float(audit.get("quality_score") or 0),
        "natural_score": float(audit.get("nat_quality_score") or 0),
        "grammar_score": float(qd.get("grammar_score") or audit.get("quality_score") or 0),
        "entity_score": float(qd.get("entity_score") or 100.0 - min(100, int(qd.get("placeholder_leak_count") or 0) * 25)),
        "integrity_score": 100.0 if not qd.get("placeholder_leak_count") else max(
            0.0, 100.0 - float(qd.get("placeholder_leak_count") or 0) * 20
        ),
        "final_score": float(audit.get("quality_score") or 0),
        "mixed_language_pct": round(float(audit.get("nat_mixed_language_pct") or qd.get("mixed_language_pct") or 0), 1),
        "enterprise": bool(audit.get("enterprise")),
    }


def _build_from_trace(
    audit: dict[str, Any],
    trace: dict[str, Any],
    *,
    task_info: dict[str, Any],
) -> dict[str, Any]:
    qd = audit.get("quality_details") or {}
    entities = list(trace.get("entities") or [])
    token_map = dict(trace.get("entity_map") or {})
    timing = trace.get("timing_ms") or {}
    mt_req = trace.get("mt_request") or {}

    original = str(trace.get("original") or audit.get("whisper_text") or "")
    preprocessed = str(trace.get("preprocessed") or original)
    masked = str(trace.get("masked_text") or "")
    raw_mt = str(trace.get("raw_mt_response") or "")
    after_restore = str(trace.get("after_restore") or "")
    after_natural = str(trace.get("after_naturalizer") or audit.get("naturalized_text") or "")
    after_grammar = str(trace.get("after_grammar") or audit.get("quality_pass_after") or after_natural)
    final = str(trace.get("final") or audit.get("final_text") or audit.get("tts_text") or "")

    stages: list[dict[str, Any]] = []
    stages.append(_stage("original", "Original", original, ms=float(timing.get("preprocessing") or 0)))
    stages.append(_stage("preprocessing", "After Preprocessing", preprocessed, ms=float(timing.get("preprocessing") or 0)))
    stages.append(_stage(
        "entity_detection",
        "After Entity Detection",
        "\n".join(entities) if entities else "(none)",
        ms=float(timing.get("entity") or 0),
        entities=entities,
        token_map=token_map,
        list_only=True,
    ))
    stages.append(_stage(
        "serialization",
        "After Placeholder Serialization",
        masked or preprocessed,
        ms=float(timing.get("entity") or 0),
        entities=entities,
        token_map=token_map,
    ))
    stages.append({
        "id": "translation_request",
        "name": "Translation Request",
        "text": "",
        "ms": float(timing.get("mt") or audit.get("duration_ms") or 0),
        "ok": True,
        "issues": [],
        "meta": {
            "engine": mt_req.get("engine") or audit.get("engine") or "",
            "route": mt_req.get("route") or f"{audit.get('source_lang', '')}→{audit.get('target_lang', '')}",
            "model": mt_req.get("model") or audit.get("model") or "",
            "router_reason": mt_req.get("router_reason") or audit.get("router_reason") or "",
        },
        "integrity": analyze_text_integrity(""),
    })
    stages.append(_stage(
        "raw_mt",
        "Raw MT Response",
        raw_mt,
        ms=float(timing.get("mt") or audit.get("duration_ms") or 0),
        entities=entities,
        token_map=token_map,
    ))
    stages.append(_stage(
        "restore",
        "After Placeholder Restore",
        after_restore or raw_mt,
        ms=float(timing.get("restore") or 0),
        entities=entities,
        token_map=token_map,
    ))
    stages.append(_stage(
        "natural",
        "After Natural Translation",
        after_natural,
        ms=float(timing.get("naturalizer") or audit.get("naturalizer_ms") or 0),
        entities=entities,
    ))
    stages.append(_stage(
        "grammar",
        "After Grammar Fix",
        after_grammar,
        ms=float(timing.get("grammar") or audit.get("quality_pass_ms") or 0),
        entities=entities,
    ))
    stages.append(_stage(
        "final",
        "Final Translation",
        final,
        ms=float(timing.get("total") or 0),
        entities=entities,
    ))

    transitions: list[dict[str, Any]] = []
    for i in range(len(stages) - 1):
        a, b = stages[i], stages[i + 1]
        if a.get("id") == "translation_request":
            continue
        if b.get("id") == "translation_request":
            continue
        ia = a.get("integrity") or analyze_text_integrity(a.get("text", ""), entities=entities, token_map=token_map)
        ib = b.get("integrity") or analyze_text_integrity(b.get("text", ""), entities=entities, token_map=token_map)
        tr = _compare_integrity(ia, ib, stage_from=a.get("name", ""), stage_to=b.get("name", ""))
        if a.get("id") == "serialization" and b.get("id") == "raw_mt":
            if token_map and _count_placeholders(a.get("text", "")) > 0 and has_mt_garbage(b.get("text", "")):
                tr["ok"] = False
                tr["issues"] = list(dict.fromkeys((tr.get("issues") or []) + ["placeholder_damaged"]))
                tr["regression"] = True
        transitions.append(tr)

    warnings = segment_quality_warnings(
        original=original,
        raw=after_restore or raw_mt,
        naturalized=after_natural,
        final=final,
        tts_text=final,
        source_lang=task_info.get("detected_lang") or task_info.get("source_lang"),
        target_lang=task_info.get("target_lang"),
    )
    for w in audit.get("validation_warnings") or []:
        if isinstance(w, dict):
            code = w.get("code", "")
            if code:
                warnings.append({"code": code, "stage": w.get("stage", "final"), "tokens": w.get("tokens", [])})

    total_ms = sum(float(timing.get(k) or 0) for k in timing) or (
        float(audit.get("duration_ms") or 0)
        + float(audit.get("naturalizer_ms") or 0)
        + float(audit.get("quality_pass_ms") or 0)
    )

    return {
        "index": int(audit.get("index", 0)) + 1,
        "original": original,
        "final": final,
        "stages": stages,
        "transitions": transitions,
        "quality": _quality_scores(audit, qd),
        "warnings": warnings,
        "timing_ms": {**timing, "total": round(total_ms, 1)},
        "engine": mt_req.get("engine") or audit.get("engine") or "",
        "route": mt_req.get("route") or "",
    }


def _build_from_audit_fallback(
    audit: dict[str, Any],
    *,
    task_info: dict[str, Any],
) -> dict[str, Any]:
    """Synthesize inspector view when pipeline did not record trace."""
    architect = audit.get("architect") or {}
    registry = architect.get("registry") or []
    entities = [str(r.get("original", "")) for r in registry if r.get("original")]
    if not entities:
        from engines.proper_nouns_dict import extra_preserved_tokens
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent
        entities = extra_preserved_tokens(str(audit.get("whisper_text") or ""), app_dir=base)

    trace = {
        "original": audit.get("whisper_text") or "",
        "preprocessed": audit.get("whisper_text") or "",
        "entities": entities,
        "entity_map": architect.get("token_map") or {},
        "masked_text": architect.get("masked") or audit.get("whisper_text") or "",
        "raw_mt_response": audit.get("raw_translation") or "",
        "after_restore": audit.get("raw_translation") or "",
        "after_naturalizer": audit.get("naturalized_text") or "",
        "after_grammar": audit.get("quality_pass_after") or audit.get("naturalized_text") or "",
        "final": audit.get("final_text") or audit.get("tts_text") or "",
        "mt_request": {
            "engine": audit.get("engine") or "",
            "route": f"{audit.get('source_lang', '')}→{audit.get('target_lang', '')}",
            "model": audit.get("model") or "",
            "router_reason": audit.get("router_reason") or "",
        },
        "timing_ms": {
            "preprocessing": 0,
            "entity": 0,
            "mt": float(audit.get("duration_ms") or 0),
            "restore": 0,
            "naturalizer": float(audit.get("naturalizer_ms") or 0),
            "grammar": float(audit.get("quality_pass_ms") or 0),
        },
    }
    qd = audit.get("quality_details") or {}
    if isinstance(qd.get("inspector"), dict):
        trace = {**trace, **qd["inspector"]}
    return _build_from_trace(audit, trace, task_info=task_info)


def build_translation_inspector(task_info: dict[str, Any]) -> dict[str, Any]:
    if not inspector_enabled():
        return {"enabled": False, "segments": []}

    audits = task_info.get("translation_audits") or []
    source_segments = task_info.get("source_segments") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}

    segments_out: list[dict[str, Any]] = []
    for i, src in enumerate(source_segments):
        audit = audit_by_idx.get(i, {})
        if not audit:
            audit = {"index": i, "whisper_text": str(src or "")}
        qd = audit.get("quality_details") or {}
        trace = qd.get("inspector") if isinstance(qd.get("inspector"), dict) else {}
        if trace:
            seg = _build_from_trace(audit, trace, task_info=task_info)
        else:
            seg = _build_from_audit_fallback(audit, task_info=task_info)
        segments_out.append(seg)

    failed = []
    for seg in segments_out:
        for tr in seg.get("transitions") or []:
            if not tr.get("ok", True) and tr.get("regression"):
                failed.append({"segment": seg.get("index"), "transition": f"{tr.get('from')} → {tr.get('to')}", "issues": tr.get("issues")})

    return {
        "enabled": True,
        "version": 1,
        "title": "Developer Translation Inspector",
        "task_id": task_info.get("task_id") or "",
        "source_lang": task_info.get("detected_lang") or task_info.get("source_lang"),
        "target_lang": task_info.get("target_lang"),
        "segment_count": len(segments_out),
        "segments": segments_out,
        "failed_transitions": failed,
    }


def export_inspector_text(report: dict[str, Any]) -> str:
    lines = [
        report.get("title") or "Developer Translation Inspector",
        f"Source: {report.get('source_lang')} → Target: {report.get('target_lang')}",
        f"Segments: {report.get('segment_count', 0)}",
        "",
    ]
    for seg in report.get("segments") or []:
        lines.append("=" * 30)
        lines.append(f"SEGMENT #{seg.get('index', '?')}")
        lines.append("")
        for st in seg.get("stages") or []:
            mark = "OK" if st.get("ok", True) else "WARN"
            if st.get("issues"):
                mark = "ERROR"
            lines.append(st.get("name", st.get("id", "")))
            if st.get("id") == "translation_request":
                meta = st.get("meta") or {}
                lines.append(f"  Engine: {meta.get('engine', '')}")
                lines.append(f"  Route: {meta.get('route', '')}")
                lines.append(f"  Model: {meta.get('model', '')}")
            elif st.get("id") == "entity_detection":
                for ent in st.get("entities") or st.get("text", "").split("\n"):
                    if ent.strip():
                        lines.append(f"  {ent.strip()}")
            else:
                txt = st.get("text", "")
                if txt:
                    lines.append(f"  {txt}")
            if st.get("ms"):
                lines.append(f"  Time: {st.get('ms')} ms")
            if st.get("issues"):
                lines.append(f"  [{mark}] {', '.join(st.get('issues') or [])}")
            lines.append("")
        q = seg.get("quality") or {}
        lines.append("Quality:")
        for k, v in q.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        timing = seg.get("timing_ms") or {}
        if timing:
            lines.append("Timing:")
            for k, v in timing.items():
                lines.append(f"  {k}: {v} ms")
            lines.append("")
        for tr in seg.get("transitions") or []:
            if not tr.get("ok", True):
                lines.append(f"TRANSITION ERROR: {tr.get('from')} → {tr.get('to')}")
                lines.append(f"  {', '.join(tr.get('issues') or [])}")
                lines.append("")
        warns = seg.get("warnings") or []
        if warns:
            lines.append("Warnings:")
            for w in warns:
                if isinstance(w, dict):
                    lines.append(f"  {w.get('code', w)}")
                else:
                    lines.append(f"  {w}")
            lines.append("")
    return "\n".join(lines)


def export_inspector_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
