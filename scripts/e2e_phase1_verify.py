#!/usr/bin/env python3
"""Phase 1 E2E verification — soft_sync, regeneration API, plugin chain, manual checklist."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "output"
CHECKLIST_PATH = OUTPUT_DIR / "phase1_e2e_checklist.txt"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def test_soft_sync_shorten_loop() -> dict[str, Any]:
    """Mock TTS returns long audio; soft_sync loop should shorten text."""
    from pydub import AudioSegment

    from engines.soft_sync import fit_segment_with_retry

    result: dict[str, Any] = {"name": "soft_sync_shorten_loop", "ok": False, "detail": ""}

    with tempfile.TemporaryDirectory(prefix="phase1_soft_sync_") as td:
        work = Path(td)
        out = work / "tts_out"
        out.mkdir()

        call_count = {"n": 0}

        def fake_generate(**kwargs):
            call_count["n"] += 1
            dur = 3200 if call_count["n"] == 1 else 1700
            p = out / f"seg_{call_count['n']}.mp3"
            AudioSegment.silent(duration=dur).export(p, format="mp3")
            return [p.name]

        import engines.tts as tts_mod

        orig_generate = tts_mod.generate_audio
        orig_output = tts_mod.OUTPUT_DIR
        tts_mod.generate_audio = fake_generate
        tts_mod.OUTPUT_DIR = out
        try:
            long_text = (
                "Это очень длинная фраза для проверки цикла soft sync, "
                "которая должна быть укорочена перед повторной генерацией."
            )
            fit = fit_segment_with_retry(
                long_text,
                voice="ru-RU-DmitryNeural",
                slot_start_ms=0,
                slot_end_ms=2000,
                lang="ru",
                work_dir=work / "sync",
                max_iterations=2,
            )
            iterations = fit.get("iterations") or []
            actions = [it.get("action") for it in iterations]
            shortened = any(a == "shorten" for a in actions) or len(iterations) >= 2
            has_overflow = "overflow_pct" in fit
            result["ok"] = bool(shortened and has_overflow and fit.get("text"))
            result["detail"] = (
                f"iterations={len(iterations)} actions={actions} "
                f"overflow_pct={fit.get('overflow_pct')} text_len={len(fit.get('text') or '')}"
            )
        finally:
            tts_mod.generate_audio = orig_generate
            tts_mod.OUTPUT_DIR = orig_output

    return result


def _prepare_flask_app():
    """Load heavy blueprints before test_client (avoids lazy-load race on first request)."""
    os.environ.setdefault("VM_DEV_MODE", "1")
    os.environ.setdefault("FEATURE_DUB_STUDIO", "1")
    from app import app as flask_app
    from engines.app_loader import ensure_heavy_blueprints
    from engines.feature_flags.manager import get_feature_manager

    ensure_heavy_blueprints(flask_app, feature_manager=get_feature_manager(ROOT))
    return flask_app


def test_regeneration_api_import() -> dict[str, Any]:
    """Import regeneration engine + studio regenerate/auto-fix API wiring."""
    result: dict[str, Any] = {"name": "regeneration_api_import", "ok": False, "detail": ""}

    try:
        import engines.regeneration as reg_mod
        from engines.regeneration import auto_fix_segment, regenerate_segment

        assert callable(regenerate_segment)
        assert callable(auto_fix_segment)
        assert hasattr(reg_mod, "regenerate_segment")

        flask_app = _prepare_flask_app()

        with tempfile.TemporaryDirectory(prefix="phase1_regen_") as td:
            app_dir = Path(td)
            out = app_dir / "output"
            out.mkdir(parents=True)

            from pydub import AudioSegment

            def fake_generate(**kwargs):
                p = out / "regen.mp3"
                AudioSegment.silent(duration=1600).export(p, format="mp3")
                return [p.name]

            import engines.tts as tts_mod

            orig_generate = tts_mod.generate_audio
            orig_output = tts_mod.OUTPUT_DIR
            tts_mod.generate_audio = fake_generate
            tts_mod.OUTPUT_DIR = out

            import api.studio_api as studio_api

            orig_app_dir = studio_api.APP_DIR
            studio_api.APP_DIR = app_dir

            session_id = f"phase1_e2e_{uuid.uuid4().hex[:8]}"
            state = {
                "session_id": session_id,
                "segments": [
                    {
                        "id": "0",
                        "index": 0,
                        "text": "Тестовый сегмент для регенерации",
                        "start_ms": 0,
                        "end_ms": 2500,
                    }
                ],
                "timing_map": [{"start": 0, "end": 2500}],
                "voice": "ru-RU-DmitryNeural",
                "lang": "ru",
            }
            studio_api._save_session(state)

            client = flask_app.test_client()
            reg_resp = client.post(
                "/api/studio/segment/0/regenerate",
                json={"session_id": session_id, "use_soft_sync": False},
            )
            fix_resp = client.post(
                "/api/studio/segment/0/auto-fix",
                json={"session_id": session_id},
            )

            tts_mod.generate_audio = orig_generate
            tts_mod.OUTPUT_DIR = orig_output
            studio_api.APP_DIR = orig_app_dir

        reg_ok = reg_resp.status_code == 200 and reg_resp.is_json
        fix_ok = fix_resp.status_code == 200 and fix_resp.is_json
        reg_data = reg_resp.get_json(silent=True) or {}
        fix_data = fix_resp.get_json(silent=True) or {}

        result["ok"] = reg_ok and fix_ok and (
            reg_data.get("file") or reg_data.get("ok") is not None
        ) and fix_data.get("ok") is not None
        result["detail"] = (
            f"regen_status={reg_resp.status_code} auto_fix_status={fix_resp.status_code} "
            f"overflow_pct={reg_data.get('overflow_pct')}"
        )
        if not result["ok"]:
            result["detail"] += f" regen_body={str(reg_data)[:120]}"
    except Exception as exc:
        result["detail"] = str(exc)

    return result


def test_plugin_chain_loudness_compressor() -> dict[str, Any]:
    """Run eq (loudnorm) + compressor chain on short wav when ffmpeg is available."""
    result: dict[str, Any] = {
        "name": "plugin_chain_loudness_compressor",
        "ok": False,
        "detail": "",
        "skipped": False,
    }

    if not _ffmpeg_available():
        result["skipped"] = True
        result["ok"] = True
        result["detail"] = "ffmpeg not in PATH — skipped (pass-through path not exercised)"
        return result

    from pydub import AudioSegment
    from engines.plugins.registry import list_plugins, process_chain

    plugins = {p["plugin_id"] for p in list_plugins()}
    if "eq" not in plugins or "compressor" not in plugins:
        result["detail"] = f"missing plugins: {plugins}"
        return result

    with tempfile.TemporaryDirectory(prefix="phase1_plugins_") as td:
        work = Path(td)
        src = work / "tone.wav"
        AudioSegment.silent(duration=400).export(src, format="wav")
        before = src.stat().st_size

        out_path = process_chain(
            src,
            ROOT,
            order=["eq", "compressor"],
            params={"eq": {"target_lufs": -16.0}, "compressor": {"threshold": -18.0, "ratio": 3.0}},
        )
        out = Path(out_path)
        if not out.is_file():
            result["detail"] = "chain returned missing file"
            return result

        changed = out.resolve() != src.resolve() or out.stat().st_size != before
        result["ok"] = changed and out.stat().st_size > 100
        result["detail"] = f"in={src.name} out={out.name} size={out.stat().st_size}"

    return result


def write_manual_checklist(results: list[dict[str, Any]]) -> Path:
    """Document manual /studio steps required for YELLOW → GREEN."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    auto_pass = sum(1 for r in results if r.get("ok") and not r.get("skipped"))
    auto_skip = sum(1 for r in results if r.get("skipped"))
    auto_fail = sum(1 for r in results if not r.get("ok"))

    lines = [
        "TubeDub Phase 1 — E2E manual checklist (/studio)",
        f"Generated: {ts}",
        f"Automated script: scripts/e2e_phase1_verify.py",
        "",
        "=== Automated results (this run) ===",
    ]
    for r in results:
        status = "SKIP" if r.get("skipped") else ("PASS" if r.get("ok") else "FAIL")
        lines.append(f"  [{status}] {r.get('name')}: {r.get('detail', '')}")
    lines.extend(
        [
            "",
            f"Summary: {auto_pass} passed, {auto_fail} failed, {auto_skip} skipped",
            "",
            "=== Environment (required for manual steps) ===",
            "  set VM_DEV_MODE=1",
            "  set FEATURE_DUB_STUDIO=1",
            "  set FEATURE_SOFT_SYNC=1",
            "  set FEATURE_WORD_TIMING=1",
            "  Optional: ensure ffmpeg in PATH; edge-tts + network for live TTS",
            "",
            "=== Manual /studio — YELLOW → GREEN ===",
            "",
            "1. Start app (python app.py or run_browser.bat).",
            "2. Open /studio — timeline visible with 7 tracks.",
            "3. Import SRT/VTT (Import button or POST /api/studio/import).",
            "4. Click a segment block → inspector shows text, overflow badge, emotion.",
            "5. Edit segment text → Save → verify session autosave under output/studio_sessions/.",
            "6. Click «Regenerate» (or POST /api/studio/segment/<id>/regenerate).",
            "   - Response JSON must include overflow_pct and file/ok.",
            "7. Click «Исправить автоматически» (POST .../auto-fix, use_soft_sync=true).",
            "   - overflow_pct should drop vs step 6 on overlong segments.",
            "8. Change emotion in inspector → PATCH /api/studio/segment/<id>/emotion.",
            "   - Segment metadata updates; optional auto-regenerate.",
            "9. GET /api/studio/plugins — eq + compressor listed.",
            "10. Full dub path: /dub with short speech video, flags ON.",
            "    - Check output/dev/feature_flags/developer.log for soft_sync / word_timing.",
            "    - Confirm timing_fit strategy in dub output logs.",
            "",
            "=== Acceptance (GREEN criteria) ===",
            "  [ ] Regenerate + auto-fix on real edge-tts audio (not mock)",
            "  [ ] Soft sync loop reduces overflow on 30s+ speech dub task",
            "  [ ] Plugin chain audibly normalizes level (optional A/B listen)",
            "  [ ] .tdproj autosave opens in project_format import",
            "  [ ] No regression on default dub with all FEATURE_* flags OFF",
            "",
            "=== Known YELLOW items (Phase 1) ===",
            "  - Plugin chain not wired into auto_dub mux (API/registry only)",
            "  - Emotion prosody is heuristic (RMS/ZCR), not pitch ML",
            "  - Session store is local JSON, single-user",
            "  - /dub-studio vs /studio are separate UIs",
        ]
    )
    CHECKLIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return CHECKLIST_PATH


def main() -> int:
    _log("Phase 1 E2E verify")
    _log(f"ROOT={ROOT}")

    results: list[dict[str, Any]] = []

    for fn in (
        test_soft_sync_shorten_loop,
        test_regeneration_api_import,
        test_plugin_chain_loudness_compressor,
    ):
        _log(f"\n--- {fn.__name__} ---")
        try:
            r = fn()
        except Exception as exc:
            r = {"name": fn.__name__, "ok": False, "detail": f"exception: {exc}"}
        results.append(r)
        status = "SKIP" if r.get("skipped") else ("PASS" if r.get("ok") else "FAIL")
        _log(f"  [{status}] {r.get('detail', '')}")

    checklist = write_manual_checklist(results)
    _log(f"\nChecklist written: {checklist}")

    failed = [r for r in results if not r.get("ok")]
    if failed:
        _log(f"\nFAILED: {len(failed)} check(s)")
        return 1
    _log("\nAll Phase 1 E2E automated checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
