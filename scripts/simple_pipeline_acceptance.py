# -*- coding: utf-8 -*-
"""Simple pipeline acceptance: ~2 min George Jr. clip → MP4.

Writes: output/simple_pipeline_acceptance.json
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

SRC_CANDIDATES = [
    APP_DIR / "uploads" / "stage2_happy_path_clip.mp4",
    APP_DIR / "uploads" / "video_076d1b49ad.mp4",
    APP_DIR / "uploads" / "video_0c3e6038c7.mp4",
]
CLIP = APP_DIR / "uploads" / "simple_pipeline_clip.mp4"
RESULT = APP_DIR / "output" / "simple_pipeline_acceptance.json"
CLIP_SEC = 110


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def ensure_clip() -> Path | None:
    for src in SRC_CANDIDATES:
        if src.is_file() and src.stat().st_size > 100_000:
            if src.resolve() == CLIP.resolve() or "stage2_happy" in src.name:
                if src.is_file():
                    return src
            break
    else:
        print("NO_SOURCE_VIDEO")
        return None
    if CLIP.is_file() and CLIP.stat().st_size > 100_000:
        return CLIP
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return src if src.is_file() else None
    CLIP.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            "10",
            "-i",
            str(src),
            "-t",
            str(CLIP_SEC),
            "-c",
            "copy",
            str(CLIP),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not CLIP.is_file():
        return src if src.is_file() else None
    return CLIP


def main() -> int:
    from app import app

    out: dict = {"pipeline": {}, "checks": {}}
    clip = ensure_clip()
    if not clip:
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    client = app.test_client()
    r = client.get("/api/license/status")
    lic = r.get_json() or {}
    if not lic.get("features", {}).get("auto_dub"):
        from engines.license_manager import activate_key, generate_key

        ok, _, msg = activate_key(generate_key("TEST-7"))
        print("license:", ok, msg)

    with open(clip, "rb") as f:
        r = client.post("/api/dub/upload_video", data={"file": (f, clip.name)})
    up = r.get_json() or {}
    print("upload", r.status_code, up)
    if r.status_code != 200:
        out["pipeline"]["error"] = "upload_failed"
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    t0 = time.time()
    r = client.post(
        "/api/auto_dub/start",
        json={
            "video_path": "uploads/" + up.get("filename", clip.name),
            "target_lang": "uk",
            "source_lang": "en",
            "voice": "uk-UA-OstapNeural",
            "model_size": "medium",
            "dub_style": "modern",
            "user_mode": "basic",
            "ui_lang": "uk",
            "tts_engine": "edge-offline",
            "translation_review_before_tts": False,
        },
    )
    start = r.get_json() or {}
    print("start", r.status_code, start)
    out["pipeline"]["start"] = {"status": r.status_code, "body": start}
    if r.status_code == 409 and start.get("error_code") == "prepare_required":
        out["pipeline"]["skipped"] = "prepare_required"
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SKIP prepare_required")
        return 0
    if r.status_code != 200:
        RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    task_id = start["task_id"]
    out["pipeline"]["task_id"] = task_id
    deadline = time.time() + 1500
    last = {}
    for i in range(1500):
        if time.time() > deadline:
            out["pipeline"]["error"] = "timeout"
            break
        r = client.get(f"/api/auto_dub/status/{task_id}")
        st = r.get_json() or {}
        last = st
        if i % 20 == 0:
            print(
                f"  [{i}s] {st.get('status')} {st.get('step_label')} "
                f"{st.get('progress')}%"
            )
        if st.get("status") == "translation_review":
            ar = client.post(
                f"/api/auto_dub/translation_review/{task_id}/approve",
                json={},
            )
            print("  review approved", ar.status_code)
            continue
        if st.get("status") == "studio_ready":
            # Safety: if auto-mix missed, force mix once.
            mx = client.post(f"/api/studio/mix/{task_id}", json={"force": True})
            print("  studio mix", mx.status_code, (mx.get_json() or {})[:1] if False else mx.status_code)
            time.sleep(2)
            continue
        if st.get("status") == "done":
            out["pipeline"]["status"] = "done"
            out["pipeline"]["output_file"] = st.get("output_file")
            break
        if st.get("status") == "error":
            out["pipeline"]["status"] = "error"
            out["pipeline"]["errors"] = st.get("errors") or st.get("error")
            break
        time.sleep(1)

    out["pipeline"]["elapsed_sec"] = round(time.time() - t0, 1)
    out["pipeline"]["last_status"] = {
        "status": last.get("status"),
        "step": last.get("step_label"),
        "progress": last.get("progress"),
    }

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
        from engines.translation_segment_parity import detect_translation_bleed

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id) or {}
            info = dict(task.get("info") or {})
        srcs = list(info.get("source_segments") or [])
        sd = list(info.get("segments_data") or [])
        texts = [
            str((s.get("text") if isinstance(s, dict) else s) or "")
            for s in sd
        ]
        if not texts:
            texts = list(info.get("translated_segments") or [])
        bleed = detect_translation_bleed(srcs, texts) if srcs and texts else []
        examples = []
        for i, src in enumerate(srcs):
            low = str(src).lower()
            if any(
                k in low
                for k in (
                    "except for cars",
                    "fiat",
                    "intersection",
                    "star wars",
                    "george lucas",
                )
            ):
                examples.append(
                    {
                        "index": i,
                        "original": str(src)[:160],
                        "translation": (texts[i] if i < len(texts) else "")[:160],
                    }
                )
        timing_rows = list(info.get("timing_fit_segments") or [])
        atempos = [float(r.get("atempo") or 1.0) for r in timing_rows]
        out["pipeline"].update(
            {
                "adaptation_path": info.get("adaptation_path"),
                "happy_path": info.get("happy_path"),
                "simple_pipeline": info.get("simple_pipeline"),
                "simple_auto_mix_done": info.get("simple_auto_mix_done"),
                "segments_before": info.get("segments_before")
                or info.get("stt_segment_count_raw"),
                "segments_after": info.get("segments_after")
                or info.get("stt_segment_count_merged"),
                "translation_parity": info.get("translation_parity"),
                "text_slot_fit": info.get("text_slot_fit"),
                "adaptive_segmentation_skipped": info.get(
                    "adaptive_segmentation_skipped"
                ),
                "tps_skipped": info.get("tps_skipped"),
                "timing_aware_skipped": info.get("timing_aware_skipped"),
                "meaning_fit_skipped": info.get("meaning_fit_skipped"),
                "examples": examples[:8],
                "timing_summary": {
                    "n": len(timing_rows),
                    "max_atempo": max(atempos) if atempos else None,
                    "atempo_over_1_08": any(a > 1.0801 for a in atempos),
                    "bleed_count": sum(1 for b in bleed if b),
                    "underfill_count": info.get("underfill_count"),
                    "underfill_unresolved_count": info.get(
                        "underfill_unresolved_count"
                    ),
                    "max_underfill_ms": info.get("max_underfill_ms"),
                    "underfill_summary": info.get("underfill_summary"),
                    "fill_rows": [
                        {
                            "idx": r.get("idx"),
                            "slot_ms": r.get("slot_ms"),
                            "tts_ms": r.get("tts_ms") or r.get("speech_ms"),
                            "fill_ratio": r.get("fill_ratio"),
                            "underfill_ms": r.get("underfill_ms"),
                            "slot_shrunk": r.get("slot_shrunk"),
                            "atempo": r.get("atempo"),
                        }
                        for r in timing_rows
                    ],
                },
            }
        )
        fill_ok_n = 0
        for r in timing_rows:
            fr = float(r.get("fill_ratio") or 0)
            if fr >= 0.80 or r.get("underfill_resolved_by_shrink"):
                fill_ok_n += 1
        fill_ok_ratio = (fill_ok_n / len(timing_rows)) if timing_rows else 0.0
        out["checks"] = {
            "mp4_done": out["pipeline"].get("status") == "done",
            "simple_path": bool(info.get("simple_pipeline") or info.get("happy_path")),
            "atempo_ok": not (out["pipeline"]["timing_summary"].get("atempo_over_1_08")),
            "bleed_ok": (out["pipeline"]["timing_summary"].get("bleed_count") or 0) == 0,
            "elapsed_under_30min": out["pipeline"]["elapsed_sec"] < 1800,
            "final_tts_locked": bool(info.get("final_tts_locked")),
            "meaning_truncated_count": sum(
                1
                for r in (info.get("text_slot_fit") or {}).get("rows") or []
                if isinstance(r, dict) and r.get("meaning_truncated")
            ),
            "review_tts_mismatch": 0,
            "fill_ok_ratio": round(fill_ok_ratio, 3),
            "fill_majority_ok": fill_ok_ratio >= 0.80,
            "underfill_count": int(info.get("underfill_count") or 0),
        }
        # Review Final == final_tts_text
        mism = 0
        fit_drift = 0
        fit_by_idx = {
            int(r.get("idx", -1)): r
            for r in (info.get("text_slot_fit") or {}).get("rows") or []
            if isinstance(r, dict)
        }
        for i, s in enumerate(sd):
            if not isinstance(s, dict):
                continue
            final = str(
                s.get("final_tts_text") or s.get("final_text") or s.get("text") or ""
            ).strip()
            spoken = str(s.get("tts_text") or s.get("text_for_tts") or final).strip()
            if final and spoken and final != spoken:
                mism += 1
            fr = fit_by_idx.get(i)
            if fr and fr.get("changed"):
                fitted = str(fr.get("text") or "").strip()
                # Allow tiny whitespace drift; reject resurrection of pre-fit length.
                if fitted and final and abs(len(final) - len(fitted)) > 24:
                    if len(final) > len(fitted) + 24:
                        fit_drift += 1
        out["checks"]["review_tts_mismatch"] = mism
        out["checks"]["review_equals_tts"] = mism == 0
        out["checks"]["fit_text_drift"] = fit_drift
        out["checks"]["fit_preserved"] = fit_drift == 0
        out["pipeline"]["final_tts_locked"] = info.get("final_tts_locked")
        out["pipeline"]["audio_trim_text_sync_skipped"] = info.get(
            "audio_trim_text_sync_skipped"
        )
        out["pipeline"]["final_tts_relocked_pre_groups"] = info.get(
            "final_tts_relocked_pre_groups"
        )
        # Stage 6 TTS speedup metrics
        _speed = {
            "tts_wall_sec": info.get("tts_wall_sec"),
            "tts_segments_total": info.get("tts_segments_total"),
            "tts_cache_hits": info.get("tts_cache_hits"),
            "tts_cache_misses": info.get("tts_cache_misses"),
            "tts_concurrency_used": info.get("tts_concurrency_used"),
            "tts_retries": info.get("tts_retries"),
            "tts_skips_existing": info.get("tts_skips_existing"),
            "detail": info.get("tts_speedup"),
            "path": info.get("tts_engine_path"),
        }
        try:
            _sf = APP_DIR / "output" / f"tts_speedup_{task_id}.json"
            if _sf.is_file():
                _sd = json.loads(_sf.read_text(encoding="utf-8"))
                for k, v in _sd.items():
                    if _speed.get(k) in (None, "", {}) and v is not None:
                        _speed[k] = v
                if _sd.get("path"):
                    _speed["path"] = _sd.get("path")
                if isinstance(_sd.get("tts_wall_sec"), (int, float)):
                    _speed["tts_wall_sec"] = _sd.get("tts_wall_sec")
        except Exception:
            pass
        out["pipeline"]["tts_speedup"] = _speed
        # Stage 7 MT speedup metrics
        _mt = {
            "mt_wall_sec": info.get("mt_wall_sec"),
            "mt_segments": info.get("mt_segments"),
            "mt_batch_size": info.get("mt_batch_size"),
            "mt_calls": info.get("mt_calls"),
            "mt_engine": info.get("mt_engine"),
            "mt_cache_hits": info.get("mt_cache_hits"),
            "mt_cache_misses": info.get("mt_cache_misses"),
            "mt_concurrency_used": info.get("mt_concurrency_used"),
            "mt_retries": info.get("mt_retries"),
            "mt_path": info.get("mt_path"),
            "detail": info.get("mt_speedup"),
        }
        try:
            _mf = APP_DIR / "output" / f"mt_speedup_{task_id}.json"
            if _mf.is_file():
                _md = json.loads(_mf.read_text(encoding="utf-8"))
                for k, v in _md.items():
                    if _mt.get(k) in (None, "", {}) and v is not None:
                        _mt[k] = v
                if isinstance(_md.get("mt_wall_sec"), (int, float)):
                    _mt["mt_wall_sec"] = _md.get("mt_wall_sec")
        except Exception:
            pass
        out["pipeline"]["mt_speedup"] = _mt
        # Stage 8 STT speedup metrics
        _stt = {
            "stt_wall_sec": info.get("stt_wall_sec"),
            "stt_model": info.get("stt_model") or info.get("model_size"),
            "stt_device": info.get("stt_device"),
            "stt_compute_type": info.get("stt_compute_type"),
            "stt_beam_size": info.get("stt_beam_size"),
            "stt_vad_filter": info.get("stt_vad_filter"),
            "stt_engine": info.get("stt_engine"),
            "stt_segments_raw": info.get("stt_segments_raw"),
            "stt_segments_after_glue": info.get("stt_segments_after_glue")
            or info.get("stt_merge_after")
            or info.get("segments_after"),
            "stt_cache_hit": info.get("stt_cache_hit"),
            "simple_stt_locked": info.get("simple_stt_locked"),
            "voice_verification_skipped": info.get("voice_verification_skipped"),
            "detail": info.get("stt_speedup"),
        }
        try:
            _stf = APP_DIR / "output" / f"stt_speedup_{task_id}.json"
            if _stf.is_file():
                _std = json.loads(_stf.read_text(encoding="utf-8"))
                for k, v in _std.items():
                    if _stt.get(k) in (None, "", {}) and v is not None:
                        _stt[k] = v
                if isinstance(_std.get("stt_wall_sec"), (int, float)):
                    _stt["stt_wall_sec"] = _std.get("stt_wall_sec")
        except Exception:
            pass
        out["pipeline"]["stt_speedup"] = _stt
        out["checks"]["stt_metrics_present"] = isinstance(
            _stt.get("stt_wall_sec"), (int, float)
        )
        out["checks"]["stt_model_ok"] = str(_stt.get("stt_model") or "") in (
            "tiny",
            "base",
            "small",
        )
        out["checks"]["stt_beam_ok"] = int(_stt.get("stt_beam_size") or 0) == 1
        out["checks"]["stt_simple_locked"] = bool(_stt.get("simple_stt_locked"))
        out["checks"]["stt_no_vv_asr"] = str(
            info.get("voice_verification_skipped") or ""
        ) == "simple_stt_lock" or info.get("voice_verification_asr_allowed") is False
        # Warm STT cache probe (same uploaded video path + model knobs)
        try:
            from engines.pipeline_cache import load_whisper_cache
            from engines.simple_stt_policy import resolve_simple_stt_model

            _m = resolve_simple_stt_model("medium")
            _vp = str(
                info.get("video_path")
                or (APP_DIR / "uploads" / up.get("filename", clip.name))
            )
            if not Path(_vp).is_file():
                _vp = str(APP_DIR / "uploads" / up.get("filename", clip.name))
            _t_warm = time.perf_counter()
            _wh2 = load_whisper_cache(
                APP_DIR,
                _vp,
                model_size=_m,
                source_lang="en",
                beam_size=int(_stt.get("stt_beam_size") or 1),
                compute_type=str(_stt.get("stt_compute_type") or "int8"),
                device=str(_stt.get("stt_device") or "cpu"),
            )
            _warm_sec = round(time.perf_counter() - _t_warm, 3)
            out["pipeline"]["stt_cache_warm"] = {
                "hit": bool(_wh2),
                "wall_sec": _warm_sec,
                "model": _m,
                "video_path": _vp,
            }
            out["checks"]["stt_cache_warm_ok"] = bool(_wh2) or bool(
                _stt.get("stt_cache_hit")
            )
        except Exception as _stt_warm_exc:
            out["pipeline"]["stt_cache_warm_error"] = str(_stt_warm_exc)
            out["checks"]["stt_cache_warm_ok"] = False
        # Stage timings from pipeline_timing JSON if present
        try:
            from pathlib import Path as _P

            _pt = _P("output") / f"pipeline_timing_{task_id}.json"
            if _pt.is_file():
                _ptj = json.loads(_pt.read_text(encoding="utf-8"))
                out["pipeline"]["stage_timings"] = _ptj.get("stages") or _ptj.get(
                    "seconds"
                ) or _ptj
        except Exception:
            pass
        out["checks"]["tts_concurrency_ok"] = int(
            info.get("tts_concurrency_used") or 0
        ) >= 5 or int(info.get("tts_segments_total") or 0) <= 3
        out["checks"]["mt_metrics_present"] = isinstance(
            _mt.get("mt_wall_sec"), (int, float)
        )
        # Cache replay: re-synth same finals → expect cache hits
        try:
            from engines.tts_cache import default_cache_dir
            from engines.tts_parallel import synthesize_segments_parallel

            replay_items = []
            voice0 = str(info.get("voice") or info.get("tts_voice") or "uk-UA-OstapNeural")
            rate0 = str(info.get("tts_rate") or "-5%")
            pitch0 = str(info.get("tts_pitch") or "")
            for i, s in enumerate(sd):
                if not isinstance(s, dict):
                    continue
                txt = str(
                    s.get("final_tts_text") or s.get("tts_text") or s.get("text") or ""
                ).strip()
                if not txt:
                    continue
                replay_items.append(
                    {
                        "index": i,
                        "text": txt,
                        "voice": voice0,
                        "out_path": str(
                            APP_DIR
                            / "output"
                            / "tts_cache_replay"
                            / f"{task_id[:8]}_{i:04d}.mp3"
                        ),
                        "rate": str(s.get("tts_synth_rate") or rate0 or "-5%"),
                        "pitch": str(s.get("tts_synth_pitch") or pitch0 or ""),
                        "engine_id": "edge-offline",
                    }
                )
            if replay_items:
                _r2, st2 = synthesize_segments_parallel(
                    replay_items,
                    concurrency=6,
                    cache_dir=default_cache_dir(),
                    warmup=0,
                    use_cache=True,
                    skip_existing=False,
                )
                out["pipeline"]["tts_cache_replay"] = st2
                out["checks"]["tts_cache_replay_hits"] = int(st2.get("tts_cache_hits") or 0)
                out["checks"]["tts_cache_ok"] = int(st2.get("tts_cache_hits") or 0) > 0
        except Exception as _creplay:
            out["pipeline"]["tts_cache_replay_error"] = str(_creplay)
            out["checks"]["tts_cache_ok"] = False
        # Stage 7 MT cache replay on source lines
        try:
            from engines.mt_batch import translate_segments_batch
            from engines.mt_cache import default_cache_dir as _mt_cdir

            srcs = []
            for s in sd:
                if not isinstance(s, dict):
                    continue
                src = str(
                    s.get("original")
                    or s.get("original_text")
                    or s.get("source_text")
                    or s.get("whisper_text")
                    or ""
                ).strip()
                if src:
                    srcs.append(src)
            if not srcs:
                srcs = [
                    str(x or "").strip()
                    for x in (info.get("source_segments") or [])
                    if str(x or "").strip()
                ]
            if srcs:
                _tr2, st_mt = translate_segments_batch(
                    srcs,
                    str(info.get("source_lang") or "en"),
                    str(info.get("target_lang") or "uk"),
                    batch_size=10,
                    cache_dir=_mt_cdir(),
                    app_dir=APP_DIR,
                    prefer_marian=True,
                )
                out["pipeline"]["mt_cache_replay"] = st_mt
                out["checks"]["mt_cache_replay_hits"] = int(st_mt.get("mt_cache_hits") or 0)
                out["checks"]["mt_cache_ok"] = int(st_mt.get("mt_cache_hits") or 0) > 0
                out["checks"]["mt_parity_ok"] = len(_tr2) == len(srcs)
        except Exception as _mreplay:
            out["pipeline"]["mt_cache_replay_error"] = str(_mreplay)
            out["checks"]["mt_cache_ok"] = False
    except Exception as exc:
        out["pipeline"]["info_error"] = str(exc)

    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", RESULT)
    print("elapsed", out["pipeline"].get("elapsed_sec"))
    print("timing", json.dumps(out["pipeline"].get("timing_summary"), ensure_ascii=False))
    print("checks", json.dumps(out.get("checks"), ensure_ascii=False))
    return 0 if out["pipeline"].get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
