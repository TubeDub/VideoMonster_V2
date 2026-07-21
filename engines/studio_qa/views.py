"""P501–P505 Studio 2.0 view builders."""

from __future__ import annotations

from typing import Any

from engines.studio_qa.types import (
    PIPELINE_STAGES,
    ReplicaStudioObject,
    ReviewScores,
)


def build_pipeline_view(
    *,
    pipeline_state: str = "",
    stage_errors: dict[str, list[str]] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """P502 — all stages with immediate error surfacing."""
    errors = stage_errors or {}
    meta = meta or {}
    stages = []
    for name in PIPELINE_STAGES:
        key = name.lower().replace(" ", "_")
        err = list(errors.get(name) or errors.get(key) or [])
        status = "error" if err else "ok"
        # Soft status from meta flags
        if name == "Semantic Lock" and meta.get("whisper_owner") is False:
            status = status if status == "error" else "ok"
        if name == "Speech" and meta.get("dub_engine_v2"):
            status = status if status == "error" else "ok"
        stages.append(
            {
                "name": name,
                "status": status,
                "errors": err,
            }
        )
    return {
        "pipeline_state": pipeline_state,
        "stages": stages,
        "has_errors": any(s["status"] == "error" for s in stages),
    }


def build_timeline_view(meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """P503 — replicas, WAV, pauses, borrow/tempo/stretch/alignment."""
    meta = meta or {}
    timeline = meta.get("timeline") or {}
    units = list(timeline.get("units") or [])
    adjustments = (meta.get("dub") or {}).get("adjustments") or []
    adj_by_speech = {a.get("speech_uuid"): a for a in adjustments if isinstance(a, dict)}
    rows = []
    for u in units:
        if not isinstance(u, dict):
            continue
        adj = adj_by_speech.get(u.get("speech_uuid")) or {}
        rows.append(
            {
                "audio_uuid": u.get("audio_uuid"),
                "speech_uuid": u.get("speech_uuid"),
                "start_ms": u.get("start_ms"),
                "end_ms": u.get("end_ms"),
                "wav": u.get("wav_path") or u.get("file") or "",
                "tempo": u.get("tempo") or adj.get("tempo") or 1.0,
                "stretch": u.get("stretch") or adj.get("stretch") or 1.0,
                "borrow_ms": adj.get("borrow_ms") or 0,
                "pause_ms": u.get("pause_ms") or adj.get("pause_ms") or 0,
                "alignment": u.get("alignment") or [],
                "merge": "sentence_merge" in (adj.get("steps_applied") or []),
            }
        )
    return {
        "timeline_uuid": timeline.get("timeline_uuid") or "",
        "units": rows,
        "pauses": list(timeline.get("pauses") or []),
        "conflicts": list(timeline.get("conflicts") or []),
    }


def build_replicas(
    sentences: list[Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> list[ReplicaStudioObject]:
    """P501 — Pipeline objects per replica."""
    meta = meta or {}
    speech = (meta.get("speech_units") or (meta.get("dub") or {}).get("speech_units") or [])
    out: list[ReplicaStudioObject] = []
    if sentences:
        for i, s in enumerate(sentences):
            sid = getattr(s, "sentence_uuid", "") or f"seg_{i}"
            owner = "Dub Engine" if getattr(s, "semantic_locked", False) else "Translation Engine"
            errs: list[str] = []
            warns: list[str] = []
            if getattr(s, "overflow_ms", 0) > 0:
                warns.append(f"overflow_ms={s.overflow_ms}")
            status = "warning" if warns else "ok"
            out.append(
                ReplicaStudioObject(
                    segment_id=getattr(s, "dub_segment_uuid", "") or sid,
                    sentence_uuid=sid,
                    speech_uuid=(speech[i].get("speech_uuid") if i < len(speech) and isinstance(speech[i], dict) else ""),
                    state=str(getattr(s, "lock_status", "") or getattr(s, "speech_status", "") or ""),
                    owner=owner,
                    uuid=sid,
                    start_ms=int(getattr(s, "start_ms", 0) or 0),
                    end_ms=int(getattr(s, "end_ms", 0) or 0),
                    status=status,
                    errors=errs,
                    warnings=warns,
                    text_preview=(getattr(s, "translated_text", None) or getattr(s, "text", "") or "")[:120],
                )
            )
        return out
    for i, u in enumerate(speech):
        if not isinstance(u, dict):
            continue
        out.append(
            ReplicaStudioObject(
                segment_id=str(u.get("sentence_uuid") or f"seg_{i}"),
                sentence_uuid=str(u.get("sentence_uuid") or ""),
                speech_uuid=str(u.get("speech_uuid") or ""),
                state=str(u.get("speech_status") or "planned"),
                owner="Dub Engine",
                uuid=str(u.get("speech_uuid") or u.get("sentence_uuid") or ""),
                start_ms=int(u.get("start_ms") or 0),
                end_ms=int(u.get("end_ms") or 0),
                text_preview=str(u.get("text") or "")[:120],
            )
        )
    return out


def build_review_panel(
    sentences: list[Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """P504 — scores per replica."""
    meta = meta or {}
    qplans = {q.get("sentence_uuid"): q for q in (meta.get("quality_plans") or []) if isinstance(q, dict)}
    rows: list[dict[str, Any]] = []
    for s in sentences or []:
        sid = getattr(s, "sentence_uuid", "")
        qp = qplans.get(sid) or {}
        tr = ((getattr(s, "context", None) or {}).get("translation_report") or {})
        meaning = float(qp.get("meaning_score") or getattr(s, "meaning_score", 100) or 100)
        entity = float(qp.get("entity_score") or getattr(s, "entity_score", 100) or 100)
        timing = float(qp.get("duration_score") or getattr(s, "timing_score", 100) or 100)
        lipsync = float(qp.get("lipsync_score") or 80)
        speech = float(qp.get("speech_score") or 90)
        conf = float(tr.get("confidence") or getattr(s, "sentence_confidence", 1.0) or 1.0)
        overall = round(
            (meaning + entity + timing + lipsync + speech + conf * 100) / 6.0,
            1,
        )
        scores = ReviewScores(
            meaning_score=meaning,
            translation_confidence=conf,
            timing_score=timing,
            lipsync_score=lipsync,
            entity_score=entity,
            speech_score=speech,
            overall_score=overall,
        )
        rows.append({"sentence_uuid": sid, "scores": scores.to_dict()})
    return rows


def build_decision_graph_view(meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """P505 — considered / rejected / accepted strategies with reasons."""
    meta = meta or {}
    graph = meta.get("decision_graph") or {}
    records = []
    for rec in graph.get("records") or []:
        if not isinstance(rec, dict):
            continue
        accepted = rec.get("accepted") or {}
        rejected = rec.get("rejected") or []
        records.append(
            {
                "sentence_uuid": rec.get("sentence_uuid"),
                "problem": rec.get("problem"),
                "considered": [
                    {
                        "label": c.get("label"),
                        "steps": c.get("steps"),
                        "score": c.get("decision_score"),
                        "cost": c.get("cost"),
                    }
                    for c in (rec.get("candidates") or [])
                    if isinstance(c, dict)
                ],
                "rejected": [
                    {
                        "label": r.get("label"),
                        "reasons": r.get("reject_reasons") or [],
                        "explanation": r.get("explanation") or "",
                    }
                    for r in rejected
                    if isinstance(r, dict)
                ],
                "accepted": {
                    "label": accepted.get("label"),
                    "steps": accepted.get("steps"),
                    "score": accepted.get("decision_score"),
                    "explanation": accepted.get("explanation") or rec.get("reason") or "",
                },
                "reason": rec.get("reason") or "",
                "rollback_path": rec.get("rollback_path") or [],
            }
        )
    return {
        "profile": graph.get("profile") or "",
        "scene_uuid": graph.get("scene_uuid") or "",
        "records": records,
        "conflicts": graph.get("conflicts") or [],
    }
