"""One-shot repair for task f6c30e50 — strip DSAL fillers, fix clauses, polish text.

Updates:
  - autodub .tdproj pipeline.segments
  - studio session segments
  - Desktop р.json diagnostic texts
  - output/dev/translation_trace.log (review-friendly dump)

Does NOT re-synthesize TTS (text-only repair). Re-run TTS/slot-fit after approve if needed.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TASK_ID = "f6c30e50bd6c444cb73ee2c26718528a"
TDPROJ = (
    ROOT
    / "projects"
    / "tdproj"
    / "e2b56875-47f2-4fab-ae91-244b7c48467e"
    / f"autodub-{TASK_ID}.tdproj"
)
STUDIO = ROOT / "output" / "studio_sessions" / f"{TASK_ID}.json"
DIAG = Path(r"c:\Users\serhii\Desktop\р.json")
TRACE = ROOT / "output" / "dev" / "translation_trace.log"
REPORT = ROOT / "output" / "dev" / f"repair_{TASK_ID}.json"


def _plain(text: str) -> str:
    """Strip SSML + combining stress for comparison; keep accents in output via polish path."""
    t = re.sub(r"<[^>]+>", "", str(text or ""))
    return " ".join(t.split()).strip()


def _set_text_fields(seg: dict, text: str) -> None:
    text = str(text or "").strip()
    for key in (
        "text",
        "plain_text",
        "translation_text",
        "translated_text",
        "final_text",
        "semantic_text",
        "grammar_text",
        "timing_text",
        "text_for_tts",
        "voice_input",
        "locked_text",
        "tts_text",
        "pre_tts_text",
        "final_tts_text",
        "text_after_adaptation",
    ):
        if key in seg or key in (
            "text",
            "plain_text",
            "translation_text",
            "final_text",
            "text_for_tts",
        ):
            seg[key] = text


def repair_text(uk: str, en: str, *, slot_ms: int, tgt_lang: str = "uk") -> tuple[str, dict]:
    from engines.dsal import adapt_duration_semantic, stamp_dsal_on_segment
    from engines.dsal.core import strip_dsal_elaboration_fillers
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    before = _plain(uk)
    # Start from semantic/naturalized base when fillers already baked in.
    cleaned = strip_dsal_elaboration_fillers(before)
    polished = apply_pre_lock_polish(cleaned, original=en)

    # Temporary segment for DSAL stamp metadata
    seg: dict = {
        "slot_ms": int(slot_ms or 0),
        "final_text": polished,
        "translation_locked": False,
    }
    result = adapt_duration_semantic(
        polished,
        source_hint=en,
        slot_ms=int(slot_ms or 0),
        tgt_lang=tgt_lang,
        allow_llm=False,
    )
    stamp_dsal_on_segment(seg, result)
    after = apply_pre_lock_polish(result.text, original=en)
    meta = {
        "before": before,
        "after": after,
        "changed": after != before,
        "dsal_method": result.method,
        "dsal_band": result.analysis.band,
        "dsal_applied": bool(result.adaptation_executed),
        "clause_coverage": result.clause_coverage,
        "stages": list(result.stages),
    }
    return after, meta


def main() -> int:
    if not TDPROJ.exists():
        print(f"MISSING tdproj: {TDPROJ}")
        return 1
    if not STUDIO.exists():
        print(f"MISSING studio: {STUDIO}")
        return 1

    td = json.loads(TDPROJ.read_text(encoding="utf-8"))
    studio = json.loads(STUDIO.read_text(encoding="utf-8"))
    diag = json.loads(DIAG.read_text(encoding="utf-8")) if DIAG.exists() else None

    sources = list(studio.get("source_segments") or [])
    pipe_segs = list((td.get("pipeline") or {}).get("segments") or [])
    studio_segs = list(studio.get("segments") or [])
    diag_segs = list((diag or {}).get("segments") or []) if diag else []

    n = max(len(pipe_segs), len(studio_segs), len(sources), len(diag_segs))
    rows = []
    changed = 0

    for i in range(n):
        pipe = pipe_segs[i] if i < len(pipe_segs) else {}
        st = studio_segs[i] if i < len(studio_segs) else {}
        dg = diag_segs[i] if i < len(diag_segs) else {}
        en = ""
        if i < len(sources):
            en = str(sources[i] or "")
        if not en:
            en = str(
                dg.get("original_text")
                or st.get("original")
                or pipe.get("source_text")
                or pipe.get("original_text")
                or ""
            )
        uk = str(
            pipe.get("final_text")
            or pipe.get("plain_text")
            or pipe.get("translation_text")
            or pipe.get("text")
            or st.get("text")
            or dg.get("translated_text")
            or ""
        )
        slot_ms = int(
            pipe.get("slot_ms")
            or st.get("end_ms", 0) - st.get("start_ms", 0)
            or dg.get("original_duration_ms")
            or dg.get("slot_ms")
            or 0
        )
        after, meta = repair_text(uk, en, slot_ms=slot_ms, tgt_lang="uk")
        meta["index"] = i + 1
        meta["en"] = en
        rows.append(meta)
        if meta["changed"]:
            changed += 1

        if pipe:
            was_locked = bool(pipe.get("translation_locked"))
            pipe["translation_locked"] = False
            _set_text_fields(pipe, after)
            pipe["dsal_applied"] = meta["dsal_applied"]
            pipe["dsal_band"] = meta["dsal_band"]
            pipe["clause_coverage"] = meta["clause_coverage"]
            pipe["repair_polish"] = True
            # Audio was synthesized from pre-repair text — always re-TTS.
            pipe["tts_status"] = "needs_regen"
            pipe["container_status"] = "text_repaired"
            pipe["tts_outdated"] = True
            if was_locked:
                pipe["translation_locked"] = True
                pipe["locked_text"] = after
        if st:
            st["text"] = after
            st["repair_polish"] = True
            st["tts_status"] = "needs_regen"
            st["tts_outdated"] = True
        if dg:
            for k in (
                "translated_text",
                "text_after_adaptation",
                "pre_tts_text",
                "final_tts_text",
            ):
                dg[k] = after
            dg["repair_polish"] = True
            dg["tts_outdated"] = True

    # Persist
    TDPROJ.write_text(json.dumps(td, ensure_ascii=False, indent=2), encoding="utf-8")
    studio["updated_ms"] = int(time.time() * 1000)
    STUDIO.write_text(json.dumps(studio, ensure_ascii=False, indent=2), encoding="utf-8")
    if diag is not None:
        DIAG.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")

    # Review-friendly trace
    TRACE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "TubeDub — Translation Review (REPAIRED)",
        "Source: en → Target: uk",
        f"Task: {TASK_ID}",
        f"Changed segments: {changed}/{n}",
        "",
    ]
    for r in rows:
        lines.append(f"--- Segment #{r['index']} ---")
        lines.append(f"Original:      {r['en']}")
        lines.append(f"Before:        {r['before']}")
        lines.append(f"Final:         {r['after']}")
        lines.append(
            f"DSAL: applied={r['dsal_applied']} band={r['dsal_band']} "
            f"clause={r['clause_coverage']} method={r['dsal_method']}"
        )
        lines.append(f"Changed:       {r['changed']}")
        if r.get("stages"):
            lines.append(f"Stages:        {', '.join(r['stages'][:12])}")
        lines.append("")
    TRACE.write_text("\n".join(lines), encoding="utf-8")

    report = {
        "task_id": TASK_ID,
        "changed": changed,
        "total": n,
        "tdproj": str(TDPROJ),
        "studio": str(STUDIO),
        "diag": str(DIAG) if DIAG.exists() else None,
        "trace": str(TRACE),
        "segments": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK repaired {changed}/{n} segments")
    print(f"tdproj: {TDPROJ}")
    print(f"studio: {STUDIO}")
    print(f"trace:  {TRACE}")
    print(f"report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
