"""Repair + optional TTS regen for a TubeDub task (George Lucas quality fixes)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _plain(text: str) -> str:
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
    cleaned = strip_dsal_elaboration_fillers(before)
    polished = apply_pre_lock_polish(cleaned, original=en)
    seg: dict = {"slot_ms": int(slot_ms or 0), "final_text": polished, "translation_locked": False}
    result = adapt_duration_semantic(
        polished,
        source_hint=en,
        slot_ms=int(slot_ms or 0),
        tgt_lang=tgt_lang,
        allow_llm=False,
    )
    stamp_dsal_on_segment(seg, result)
    after = apply_pre_lock_polish(result.text, original=en)
    return after, {
        "before": before,
        "after": after,
        "changed": after != before,
        "dsal_method": result.method,
        "dsal_band": result.analysis.band,
        "dsal_applied": bool(result.adaptation_executed),
        "clause_coverage": result.clause_coverage,
        "stages": list(result.stages),
    }


def _find_autodub(task_id: str) -> Path | None:
    hits = list((ROOT / "projects" / "tdproj").glob(f"*/autodub-{task_id}.tdproj"))
    return hits[0] if hits else None


def _pick_voice(state: dict) -> str:
    voice = str(state.get("voice") or "").strip()
    lang = str(state.get("lang") or "uk").split("-")[0].lower()
    if lang == "uk" and (not voice or voice.startswith("ru-")):
        return "uk-UA-OstapNeural"
    return voice or "uk-UA-OstapNeural"


def repair_task(task_id: str, *, diag_path: Path | None = None) -> dict:
    studio_path = ROOT / "output" / "studio_sessions" / f"{task_id}.json"
    tdproj_path = _find_autodub(task_id)
    if not studio_path.exists():
        raise FileNotFoundError(studio_path)

    studio = json.loads(studio_path.read_text(encoding="utf-8"))
    td = json.loads(tdproj_path.read_text(encoding="utf-8")) if tdproj_path else None
    diag = json.loads(diag_path.read_text(encoding="utf-8")) if diag_path and diag_path.exists() else None

    sources = list(studio.get("source_segments") or [])
    studio_segs = list(studio.get("segments") or [])
    pipe_segs = list((td.get("pipeline") or {}).get("segments") or []) if td else []
    diag_segs = list((diag or {}).get("segments") or []) if diag else []
    n = max(len(studio_segs), len(pipe_segs), len(sources), len(diag_segs))

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
            en = str(dg.get("original_text") or st.get("original") or "")
        uk = str(
            st.get("text")
            or pipe.get("final_text")
            or pipe.get("text")
            or dg.get("translated_text")
            or ""
        )
        slot_ms = int(
            pipe.get("slot_ms")
            or (st.get("end_ms", 0) - st.get("start_ms", 0))
            or dg.get("original_duration_ms")
            or dg.get("slot_ms")
            or 0
        )
        after, meta = repair_text(uk, en, slot_ms=slot_ms)
        meta["index"] = i + 1
        meta["en"] = en
        rows.append(meta)
        if meta["changed"]:
            changed += 1
        if st:
            st["text"] = after
            st["repair_polish"] = True
            st["tts_status"] = "needs_regen"
            st["tts_outdated"] = True
        if pipe:
            was_locked = bool(pipe.get("translation_locked"))
            pipe["translation_locked"] = False
            _set_text_fields(pipe, after)
            pipe["dsal_applied"] = meta["dsal_applied"]
            pipe["dsal_band"] = meta["dsal_band"]
            pipe["clause_coverage"] = meta["clause_coverage"]
            pipe["repair_polish"] = True
            pipe["tts_status"] = "needs_regen"
            pipe["tts_outdated"] = True
            if was_locked:
                pipe["translation_locked"] = True
                pipe["locked_text"] = after
        if dg:
            for k in ("translated_text", "text_after_adaptation", "pre_tts_text", "final_tts_text"):
                dg[k] = after
            dg["repair_polish"] = True
            dg["tts_outdated"] = True

    studio["updated_ms"] = int(time.time() * 1000)
    studio_path.write_text(json.dumps(studio, ensure_ascii=False, indent=2), encoding="utf-8")
    if td and tdproj_path:
        td["updated_ms"] = studio["updated_ms"]
        tdproj_path.write_text(json.dumps(td, ensure_ascii=False, indent=2), encoding="utf-8")
    if diag and diag_path:
        diag_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")

    trace = ROOT / "output" / "dev" / "translation_trace.log"
    lines = [
        "TubeDub — Translation Review (REPAIRED)",
        "Source: en → Target: uk",
        f"Task: {task_id}",
        f"Changed segments: {changed}/{n}",
        "",
    ]
    for r in rows:
        lines += [
            f"--- Segment #{r['index']} ---",
            f"Original:      {r['en']}",
            f"Before:        {r['before']}",
            f"Final:         {r['after']}",
            f"DSAL: applied={r['dsal_applied']} band={r['dsal_band']} "
            f"clause={r['clause_coverage']} method={r['dsal_method']}",
            f"Changed:       {r['changed']}",
            "",
        ]
    trace.write_text("\n".join(lines), encoding="utf-8")

    report = {
        "task_id": task_id,
        "changed": changed,
        "total": n,
        "studio": str(studio_path),
        "tdproj": str(tdproj_path) if tdproj_path else None,
        "diag": str(diag_path) if diag_path else None,
        "segments": rows,
    }
    report_path = ROOT / "output" / "dev" / f"repair_{task_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def regen_tts(task_id: str) -> dict:
    from engines.regeneration import regenerate_segment

    studio_path = ROOT / "output" / "studio_sessions" / f"{task_id}.json"
    state = json.loads(studio_path.read_text(encoding="utf-8"))
    segments = list(state.get("segments") or [])
    timing = list(state.get("timing_map") or [])
    sources = list(state.get("source_segments") or [])
    lang = str(state.get("lang") or "uk")
    voice = _pick_voice(state)
    state["voice"] = voice
    results = []
    ok_n = fail_n = 0
    t0 = time.time()
    for i, seg in enumerate(segments):
        src = sources[i] if i < len(sources) else str(seg.get("original") or "")
        print(f"[{i+1}/{len(segments)}] regenerating…", flush=True)
        try:
            result = regenerate_segment(
                seg,
                timing_map=timing,
                voice=voice,
                lang=lang,
                source_hint=src,
                app_dir=ROOT,
                use_soft_sync=True,
            )
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        ok = bool(result.get("ok")) or bool(result.get("file"))
        if ok:
            ok_n += 1
            seg["tts_status"] = "ok"
            seg["tts_outdated"] = False
        else:
            fail_n += 1
            seg["tts_status"] = "error"
        results.append(
            {
                "index": i + 1,
                "ok": ok,
                "error": result.get("error"),
                "file": result.get("file") or seg.get("file"),
                "tts_ms": result.get("tts_ms") or seg.get("tts_ms"),
                "overflow_pct": result.get("overflow_pct") or seg.get("overflow_pct"),
            }
        )
        print(f"  -> ok={ok} file={results[-1]['file']}", flush=True)

    state["updated_ms"] = int(time.time() * 1000)
    studio_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    tdproj_path = _find_autodub(task_id)
    if tdproj_path:
        td = json.loads(tdproj_path.read_text(encoding="utf-8"))
        pipe = list((td.get("pipeline") or {}).get("segments") or [])
        for i, seg in enumerate(segments):
            if i >= len(pipe):
                break
            for k in (
                "text",
                "file",
                "fitted_file",
                "tts_ms",
                "fitted_ms",
                "overflow_ms",
                "overflow_pct",
                "container_status",
                "tts_status",
            ):
                if seg.get(k) is not None:
                    pipe[i][k] = seg.get(k)
            pipe[i]["tts_outdated"] = False
        tdproj_path.write_text(json.dumps(td, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "task_id": task_id,
        "voice": voice,
        "ok": ok_n,
        "failed": fail_n,
        "total": len(segments),
        "elapsed_sec": round(time.time() - t0, 1),
        "segments": results,
    }
    path = ROOT / "output" / "dev" / f"tts_regen_{task_id}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE ok={ok_n} fail={fail_n} voice={voice} elapsed={report['elapsed_sec']}s")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--diag", default="")
    ap.add_argument("--tts", action="store_true")
    args = ap.parse_args()
    diag = Path(args.diag) if args.diag else Path(r"c:\Users\serhii\Desktop\q.json")
    if not diag.exists():
        diag = None
    report = repair_task(args.task_id, diag_path=diag)
    print(f"OK repaired {report['changed']}/{report['total']} → {report['report_path']}")
    if args.tts:
        regen_tts(args.task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
