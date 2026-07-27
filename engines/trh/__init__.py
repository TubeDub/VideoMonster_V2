"""TRH — Translation Recovery Hotfix: audit trail + explainability."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.trh")


def stamp_segment_recovery(
    seg: dict[str, Any],
    *,
    original: str,
    raw_mt: str,
    naturalized: str,
    retry_text: str = "",
    judge_text: str = "",
    approved: str = "",
    dirty: dict[str, Any] | None = None,
    naturalizer_meta: dict[str, Any] | None = None,
    tps_path: str = "fast",
    tqe_status: str = "PASS",
    reason_codes: list[str] | None = None,
    retry_count: int = 0,
    judge_used: bool = False,
    dsal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """TRH BUG8/9 — full per-segment recovery report on segment dict."""
    nat_meta = dict(naturalizer_meta or {})
    dirty_d = dict(dirty or {})
    raw = str(raw_mt or "").strip()
    nat = str(naturalized or "").strip()
    # Keep FULL texts here — [:500] previews must never become Final/TTS source.
    approved_full = str(approved or "").strip()
    report = {
        "original": str(original or ""),
        "raw_mt": raw,
        "naturalized": nat,
        "retry": str(retry_text or "").strip(),
        "judge": str(judge_text or "").strip(),
        "approved": approved_full,
        # Optional short previews for logs/UI cards only (never sync into Final)
        "original_preview": str(original or "")[:500],
        "raw_mt_preview": raw[:500],
        "naturalized_preview": nat[:500],
        # Never fake an approved preview from dirty nat/raw on FAIL
        "approved_preview": (approved_full[:500] if approved_full else ""),
        "dirty_mt_score": dirty_d.get("dirty_mt_score", dirty_d.get("score")),
        "dirty": bool(dirty_d.get("dirty")),
        "dirty_reasons": list(dirty_d.get("reasons") or []),
        "english_leak": "english_leak" in (dirty_d.get("reasons") or [])
        or "en_word_leak" in (dirty_d.get("reasons") or []),
        "naturalizer_called": True,
        "naturalizer_applied": bool(nat_meta.get("naturalizer_applied", nat != raw)),
        "naturalizer_skip_reason": str(nat_meta.get("naturalizer_skip_reason") or ""),
        "rules_applied": list(nat_meta.get("problems") or [])
        or list(
            # reasons often live on sibling field from polish_lines
            (nat_meta.get("reasons") if isinstance(nat_meta.get("reasons"), list) else [])
            or []
        ),
        "llm_used": bool(nat_meta.get("retried") or judge_used),
        "changed_text": nat != raw,
        "retry_count": int(retry_count),
        "judge_used": bool(judge_used),
        "tps_path": tps_path,
        "route": tps_path if tps_path and tps_path != "fast" else (
            "retry" if retry_count else ("direct" if nat == raw and not dirty_d.get("dirty") else "naturalizer")
        ),
        "tqe_status": tqe_status,
        "reason_codes": list(reason_codes or []),
        "dsal_applied": bool((dsal or {}).get("applied") or seg.get("dsal_applied")),
        "dsal_skip_reason": str(
            (dsal or {}).get("skip_reason")
            or seg.get("dsal_skip_reason")
            or ""
        ),
        "entities": list(nat_meta.get("restored_entities") or []),
        "quality_score": nat_meta.get("quality_score"),
    }
    # Forbid silent direct on dirty
    if report["dirty"] and report["route"] == "direct":
        report["route"] = "naturalizer" if report["changed_text"] else "blocked"
    seg["trh"] = report
    seg["raw_mt"] = raw
    seg["naturalized_text"] = nat
    seg["translated_text"] = raw  # keep raw MT identity
    return report


def heal_truncated_final(final: str, naturalized: str, *, min_gain: int = 20) -> str:
    """If Final is a truncated prefix of Naturalized (legacy [:500] bug), restore full text."""
    fin = str(final or "").strip()
    nat = str(naturalized or "").strip()
    if not nat:
        return fin
    if not fin:
        return nat
    if len(nat) <= len(fin):
        return fin
    # Exact prefix truncate (classic 500-char cut)
    if nat.startswith(fin) and (len(nat) - len(fin)) >= min_gain:
        return nat
    # Near-prefix: Final cut mid-word / mid-clause
    if len(fin) >= 200 and nat.startswith(fin[: max(80, len(fin) - 40)]):
        if (len(nat) - len(fin)) >= min_gain:
            return nat
    return fin


def sync_audits_trh(info: dict[str, Any]) -> None:
    """TRH: full audit sync — Raw ≠ Nat when naturalizer ran; route from tps_path."""
    segments = list(info.get("segments_data") or [])
    audits = list(info.get("translation_audits") or [])
    by_idx = {int(a.get("index", -1)): a for a in audits}
    traces: list[dict[str, Any]] = []

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        trh = dict(seg.get("trh") or {})
        raw = str(
            seg.get("raw_mt")
            or trh.get("raw_mt")
            or seg.get("translated_text")
            or ""
        ).strip()
        nat = str(
            seg.get("naturalized_text")
            or trh.get("naturalized")
            or ""
        ).strip()
        # Prefer live segment fields over trh snapshots. Never let a truncated
        # trh.approved preview clobber Final/TTS (manual path used to fall back
        # to nat[:500] and wipe the rest of long segments).
        # CJK hallucination blocked for TTS: do not resurrect rejected text.
        tts_blocked = bool(seg.get("tts_blocked") or seg.get("skip_tts"))
        rejected = str(seg.get("rejected_translation") or "").strip()
        if tts_blocked:
            approved = ""
        else:
            approved = str(
                seg.get("approved_text")
                or seg.get("final_text")
                or seg.get("voice_input")
                or seg.get("text_for_tts")
                or nat
                or trh.get("approved")
                or raw
            ).strip()
            approved = heal_truncated_final(approved, nat)
            # Never restore a known rejected hallucination via nat/raw fallback
            if rejected and approved == rejected and not str(seg.get("approved_text") or "").strip():
                if seg.get("needs_manual_review"):
                    approved = str(seg.get("final_text") or seg.get("text") or "").strip()
        # Heal segment fields in-place so Review/TTS see full text even on old tasks
        if approved and approved == nat and len(nat) > 500:
            for key in (
                "final_text",
                "voice_input",
                "text_for_tts",
                "text",
                "plain_text",
            ):
                cur = str(seg.get(key) or "").strip()
                if cur and cur != approved and nat.startswith(cur):
                    seg[key] = approved
            # Manual path keeps approved_text empty until operator locks —
            # still keep final_text healed for TTS.
            if not str(seg.get("approved_text") or "").strip():
                seg["final_text"] = approved
            elif nat.startswith(str(seg.get("approved_text") or "").strip()):
                if len(nat) > len(str(seg.get("approved_text") or "")):
                    seg["approved_text"] = approved
                    for key in (
                        "final_text",
                        "voice_input",
                        "text_for_tts",
                        "text",
                        "plain_text",
                    ):
                        seg[key] = approved
        path = str(seg.get("tps_path") or trh.get("tps_path") or "fast")
        # Map TPS path → Review route (never silent direct on fail/dirty)
        route = path
        if path == "fast":
            if trh.get("dirty") and not trh.get("changed_text"):
                route = "blocked"
            elif trh.get("changed_text") or (nat and nat != raw):
                route = "naturalizer"
            else:
                route = "direct"
        elif path == "llm_judge":
            route = "judge"
        elif path == "manual":
            route = "manual"
        elif path == "retry":
            route = "retry"

        row = by_idx.get(i)
        if row is None:
            row = {"index": i}
            audits.append(row)
            by_idx[i] = row

        if raw:
            row["raw_translation"] = raw
        if nat:
            row["naturalized_text"] = nat
        elif approved and approved != raw:
            row["naturalized_text"] = approved
        if approved:
            row["final_text"] = approved
            row["tts_text"] = approved
            row["approved_text"] = approved
        elif tts_blocked:
            row["final_text"] = ""
            row["tts_text"] = ""
            row["approved_text"] = ""
            if rejected:
                row["rejected_translation"] = rejected
            row["tts_blocked"] = True
            row["tts_blocked_reason"] = str(
                seg.get("tts_blocked_reason") or "manual_fail"
            )

        row["route"] = route
        row["route_label"] = route
        row["tps_path"] = path
        row["tqe_status"] = seg.get("tqe_status") or trh.get("tqe_status") or row.get(
            "tqe_status"
        )
        row["naturalizer_executed"] = True
        row["naturalizer_applied"] = bool(
            trh.get("naturalizer_applied")
            or (nat and raw and nat != raw)
            or (approved and raw and approved != raw)
        )
        row["naturalizer_skip_reason"] = str(trh.get("naturalizer_skip_reason") or "")
        row["dirty_mt_score"] = trh.get("dirty_mt_score")
        row["dirty_mt"] = bool(trh.get("dirty"))
        row["retry_count"] = int(trh.get("retry_count") or 0)
        row["judge_used"] = bool(trh.get("judge_used"))
        row["trh"] = trh
        row["dsal_applied"] = bool(seg.get("dsal_applied"))
        row["dsal_skip_reason"] = str(seg.get("dsal_skip_reason") or "")
        if trh.get("rules_applied"):
            row["naturalizer_reasons"] = list(trh["rules_applied"])

        traces.append({"index": i, **trh, "route": route, "approved": approved[:300]})

    info["translation_audits"] = audits
    info["segments_data"] = segments
    info["trh_segment_traces"] = traces
    info["naturalizer_executed"] = True
    info["naturalizer_applied"] = any(
        bool((s.get("trh") or {}).get("naturalizer_applied"))
        for s in segments
        if isinstance(s, dict)
    )


def write_segment_trace(
    app_dir: Path | str,
    task_id: str,
    traces: list[dict[str, Any]],
    *,
    session_dir: Path | str | None = None,
) -> Path | None:
    """Persist TRH explainability JSON."""
    try:
        base = Path(app_dir)
        out_dirs = []
        if session_dir:
            out_dirs.append(Path(session_dir))
        out_dirs.append(base / "output" / "sessions" / str(task_id))
        out_dirs.append(base / "quality" / "analytics")
        path = None
        payload = {
            "schema": "tubedub.trh.segment_trace.v1",
            "task_id": task_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "segments": traces,
        }
        for d in out_dirs:
            d.mkdir(parents=True, exist_ok=True)
            path = d / (
                "trh_segment_trace.json"
                if "analytics" not in str(d)
                else f"trh_segment_trace_{task_id}.json"
            )
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        logger.info("[TRH] segment_trace written (%d segs) %s", len(traces), path)
        return path
    except Exception as exc:
        logger.debug("TRH segment_trace write failed: %s", exc)
        return None
