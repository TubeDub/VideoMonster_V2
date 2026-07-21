"""Regenerate TTS for repaired studio session f6c30e50 (all 20 segments)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TASK_ID = "f6c30e50bd6c444cb73ee2c26718528a"
SESSION = ROOT / "output" / "studio_sessions" / f"{TASK_ID}.json"
TDPROJ = (
    ROOT
    / "projects"
    / "tdproj"
    / "e2b56875-47f2-4fab-ae91-244b7c48467e"
    / f"autodub-{TASK_ID}.tdproj"
)
REPORT = ROOT / "output" / "dev" / f"tts_regen_{TASK_ID}.json"


def _pick_voice(state: dict) -> str:
    voice = str(state.get("voice") or "").strip()
    lang = str(state.get("lang") or "uk").split("-")[0].lower()
    # Session sometimes defaults to RU voice with UK lang — fix for UK dub.
    if lang == "uk" and (not voice or voice.startswith("ru-")):
        return "uk-UA-OstapNeural"
    if lang == "ru" and (not voice or voice.startswith("uk-")):
        return "ru-RU-DmitryNeural"
    return voice or "uk-UA-OstapNeural"


def main() -> int:
    from engines.regeneration import regenerate_segment

    if not SESSION.exists():
        print(f"MISSING session: {SESSION}")
        return 1

    state = json.loads(SESSION.read_text(encoding="utf-8"))
    segments = list(state.get("segments") or [])
    timing = list(state.get("timing_map") or [])
    sources = list(state.get("source_segments") or [])
    lang = str(state.get("lang") or "uk")
    voice = _pick_voice(state)
    state["voice"] = voice

    results = []
    ok_n = 0
    fail_n = 0
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
            result = {"ok": False, "error": str(exc), "segment_index": i}
        ok = bool(result.get("ok")) or bool(result.get("file"))
        if ok:
            ok_n += 1
            seg["tts_status"] = "ok"
            seg["tts_outdated"] = False
            seg["repair_polish"] = True
        else:
            fail_n += 1
            seg["tts_status"] = "error"
        results.append(
            {
                "index": i + 1,
                "ok": ok,
                "error": result.get("error"),
                "file": result.get("file") or seg.get("file"),
                "fitted_file": result.get("fitted_file") or seg.get("fitted_file"),
                "tts_ms": result.get("tts_ms") or seg.get("tts_ms"),
                "fitted_ms": result.get("fitted_ms") or seg.get("fitted_ms"),
                "overflow_pct": result.get("overflow_pct") or seg.get("overflow_pct"),
                "text": (seg.get("text") or "")[:120],
            }
        )
        print(
            f"  -> ok={ok} tts_ms={results[-1]['tts_ms']} "
            f"overflow={results[-1]['overflow_pct']}% file={results[-1]['file']}",
            flush=True,
        )

    state["updated_ms"] = int(time.time() * 1000)
    state["tts_regenerated_ms"] = state["updated_ms"]
    SESSION.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # Mirror file paths into autodub tdproj if present
    if TDPROJ.exists():
        td = json.loads(TDPROJ.read_text(encoding="utf-8"))
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
        td["updated_ms"] = state["updated_ms"]
        TDPROJ.write_text(json.dumps(td, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "task_id": TASK_ID,
        "voice": voice,
        "lang": lang,
        "ok": ok_n,
        "failed": fail_n,
        "total": len(segments),
        "elapsed_sec": round(time.time() - t0, 1),
        "session": str(SESSION),
        "segments": results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DONE ok={ok_n} fail={fail_n} voice={voice} elapsed={report['elapsed_sec']}s")
    print(f"report: {REPORT}")
    return 0 if fail_n == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
