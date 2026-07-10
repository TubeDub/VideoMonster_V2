"""Studio API — import/export, timeline, segment regenerate, auto-fix."""

from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request, send_file, abort

logger = logging.getLogger(__name__)

from engines.subtitle_formats import (
    SubtitleSegment,
    export_srt,
    export_vtt,
    parse_subtitles,
    segments_from_payload,
    segments_to_text,
    segments_to_timing_map,
)

APP_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = APP_DIR / "output"
UPLOADS_DIR = APP_DIR / "uploads"
REDBUB_DIR = UPLOADS_DIR / "redub"
STUDIO_STATE_DIR = APP_DIR / "output" / "studio_sessions"
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
REDBUB_DIR.mkdir(exist_ok=True)
STUDIO_STATE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED = {".srt", ".vtt", ".ass", ".ssa", ".txt"}

bp = Blueprint("studio_api", __name__)

_LOCK = threading.RLock()

_TRACK_DEFS = [
    {"id": "original", "label": "Original Voice", "i18n_key": "studio.track.original", "color": "#22c55e"},
    {"id": "user_voice", "label": "User Voice", "i18n_key": "studio.track.user_voice", "color": "#ec4899"},
    {"id": "music", "label": "Music", "i18n_key": "studio.track.music", "color": "#a855f7"},
    {"id": "sfx", "label": "SFX", "i18n_key": "studio.track.sfx", "color": "#64748b"},
    {"id": "tts", "label": "Dub Voice", "i18n_key": "studio.track.dub", "color": "#f59e0b"},
]


def _dub_studio_visible() -> bool:
    try:
        from engines.core.feature_flags import is_developer, is_enabled
        from engines.core.module_registry import get_module_status, ModuleStatus

        if is_developer():
            return True
        if not is_enabled("dub_studio", developer_session=False):
            return False
        st = get_module_status("dub_studio", APP_DIR)
        return st == ModuleStatus.GREEN
    except Exception:
        return False


def _guard_studio():
    if not _dub_studio_visible():
        return "Dub Studio недоступен (FEATURE_DUB_STUDIO / module status)"
    return None


def _task_info_for(task_id: str | None) -> dict[str, Any]:
    if not task_id:
        return {}
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                return dict(task.get("info") or {})
    except ImportError:
        pass
    return {}


def _resolve_task_audio(filename: str | None, *, task_id: str | None = None) -> Path:
    from engines.dubbing_engine.session_adapter import resolve_session_audio

    return resolve_session_audio(
        filename,
        task_info=_task_info_for(task_id),
        default_dir=OUTPUT_DIR,
    )


def _artifacts_dir_for(task_id: str | None) -> Path:
    from engines.dubbing_engine.session_adapter import get_active_artifacts_dir

    return get_active_artifacts_dir(OUTPUT_DIR, task_info=_task_info_for(task_id))


def _auto_dub_task(task_id: str):
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            return AUTO_TASKS.get(task_id)
    except Exception:
        return None


def _is_post_dub_session(session_id: str | None) -> bool:
    if not session_id or session_id == "default":
        return False
    sid = str(session_id)
    if _auto_dub_task(sid) is not None:
        return True
    path = _session_path(sid)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return bool(
                data.get("task_id")
                or data.get("video_preview")
                or data.get("video_path")
                or data.get("segments")
            )
        except Exception:
            pass
    return False


def _studio_access(session_id: str | None = None) -> str | None:
    """Post-dub sessions and Basic tier dub editing — no premium gate."""
    if _is_post_dub_session(session_id):
        return None
    try:
        from engines.license_manager import has_feature

        if has_feature("manual_dub") or has_feature("open_projects"):
            return None
    except Exception:
        pass
    return _guard_studio()


def _parse_timing(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        return int(item.get("start", item.get("start_ms", 0))), int(
            item.get("end", item.get("end_ms", 0))
        )
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), int(item[1])
    return 0, 0


def _audio_duration_ms(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        from pydub import AudioSegment

        return len(AudioSegment.from_file(str(path)))
    except Exception:
        return 0


def _video_preview_name(video_path: str | None) -> str | None:
    if not video_path:
        return None
    p = Path(video_path)
    if not p.is_file():
        return None
    uploads = UPLOADS_DIR.resolve()
    try:
        if uploads in p.resolve().parents or p.parent.resolve() == uploads:
            return p.name
    except OSError:
        pass
    return p.name


def build_session_from_auto_dub_task(task_id: str) -> dict[str, Any] | None:
    """Build studio session JSON from in-memory auto_dub task."""
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    except ImportError:
        return None

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return None
        info = copy.deepcopy(task.get("info") or {})
        segments_data = copy.deepcopy(info.get("segments_data") or [])
        timing_map = copy.deepcopy(info.get("timing_map_backup") or [])
        source_segments = list(info.get("source_segments") or [])
        status = task.get("status")
        output_file = task.get("output_file")

    video_path = info.get("video_path_backup") or ""
    video_preview = _video_preview_name(video_path)
    voice = str(info.get("voice") or info.get("tts_voice") or "ru-RU-DmitryNeural")
    lang = str(info.get("target_lang") or "ru")
    duration_ms = int(info.get("target_duration_ms") or 0)

    studio_segments: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        start_ms, end_ms = _parse_timing(timing_map[idx] if idx < len(timing_map) else {})
        if seg.get("start_ms") is not None:
            start_ms = int(seg["start_ms"])
        if seg.get("end_ms") is not None:
            end_ms = int(seg["end_ms"])

        fitted_ms = int(seg.get("fitted_ms") or 0)
        tts_ms = int(seg.get("tts_ms") or 0)
        visual_ms = fitted_ms or tts_ms
        tts_file = seg.get("file")
        if not visual_ms and tts_file:
            visual_ms = _audio_duration_ms(_resolve_task_audio(tts_file, task_id=task_id))
        if not tts_ms:
            tts_ms = visual_ms

        slot_ms = max(1, end_ms - start_ms)
        try:
            from engines.timing_fit import DUB_SLOT_TOLERANCE_MS as _TOL
        except Exception:
            _TOL = 75
        overflow_ms = max(0, visual_ms - (slot_ms + _TOL))
        overflow_pct = round(100.0 * overflow_ms / slot_ms, 1)

        studio_segments.append(
            {
                "id": str(seg.get("segment_id") or idx),
                "index": idx,
                "segment_id": str(seg.get("segment_id") or idx),
                "text": str(seg.get("text") or "").strip(),
                "original": source_segments[idx] if idx < len(source_segments) else "",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "file": tts_file,
                "fitted_file": seg.get("fitted_file"),
                "tts_ms": tts_ms,
                "fitted_ms": fitted_ms or visual_ms,
                "overflow_ms": overflow_ms,
                "overflow_pct": overflow_pct,
                "container_status": seg.get("container_status")
                or _overflow_status(overflow_pct),
                "tts_status": seg.get("tts_status"),
                "tts_error": seg.get("tts_error"),
                "emotion": seg.get("emotion") or "neutral",
                "timing_meta": seg.get("timing_meta") or {},
            }
        )
        duration_ms = max(duration_ms, end_ms + 500)

    if not duration_ms and studio_segments:
        duration_ms = studio_segments[-1]["end_ms"] + 2000
    if not duration_ms:
        duration_ms = 120000

    timing_out = [
        {"start": s["start_ms"], "end": s["end_ms"]} for s in studio_segments
    ]

    original_audio = info.get("original_audio_path") or info.get("extracted_audio")
    original_name = Path(original_audio).name if original_audio else None

    state: dict[str, Any] = {
        "session_id": task_id,
        "task_id": task_id,
        "segments": studio_segments,
        "timing_map": timing_out,
        "source_segments": source_segments,
        "tracks": _default_tracks(),
        "voice": voice,
        "lang": lang,
        "plugin_order": ["eq", "compressor"],
        "duration_ms": duration_ms,
        "video_path": video_path,
        "video_preview": video_preview,
        "original_audio": original_name,
        "task_status": status,
        "output_file": output_file,
        "studio_url": f"/studio?task_id={task_id}",
    }
    return state


def publish_studio_ready(task_id: str) -> str | None:
    """Persist studio session and mark auto_dub task studio_ready (post-TTS)."""
    state = build_session_from_auto_dub_task(task_id)
    if not state:
        return None
    _save_session(state)
    studio_url = state["studio_url"]
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if not task:
                return studio_url
            info = task.setdefault("info", {})
            info["studio_session_id"] = task_id
            info["studio_url"] = studio_url
            info["keep_studio_assets"] = True
            task["status"] = "studio_ready"
            task["step"] = "studio"
            # Expose DDF URL so Studio UI can show warning count
            ddf_url = info.get("ddf_url") or f"/api/ddf/{task_id}"
            info["ddf_url"] = ddf_url
            info.setdefault("ddf_warnings", 0)
            from engines.dub_task_state import touch_task

            touch_task(task_id)
    except ImportError:
        pass
    return studio_url


def _sync_session_to_auto_dub(session_id: str, state: dict[str, Any]) -> None:
    if not _is_post_dub_session(session_id):
        return
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    except ImportError:
        return

    segments = state.get("segments") or []
    timing_map = state.get("timing_map") or []
    with STATE_LOCK:
        task = AUTO_TASKS.get(session_id)
        if not task:
            return
        info = task.setdefault("info", {})
        seg_data = list(info.get("segments_data") or [])
        for seg in segments:
            idx = int(seg.get("index", seg.get("id", 0)))
            while len(seg_data) <= idx:
                seg_data.append({"index": len(seg_data), "text": "", "file": None})
            row = seg_data[idx]
            row["text"] = seg.get("text") or row.get("text")
            row["start_ms"] = seg.get("start_ms")
            row["end_ms"] = seg.get("end_ms")
            row["file"] = seg.get("file") or row.get("file")
            row["fitted_file"] = seg.get("fitted_file") or row.get("fitted_file")
            row["tts_ms"] = seg.get("tts_ms")
            row["overflow_pct"] = seg.get("overflow_pct")
            row["container_status"] = seg.get("container_status")
            row["timing_meta"] = seg.get("timing_meta") or row.get("timing_meta")
        info["segments_data"] = seg_data
        if timing_map:
            info["timing_map_backup"] = timing_map
        if state.get("source_segments"):
            info["source_segments"] = state["source_segments"]


def _default_tracks() -> dict[str, dict]:
    return {
        t["id"]: {
            "muted": False,
            "solo": False,
            "volume": 1.0,
            "label": t["label"],
            "i18n_key": t.get("i18n_key", ""),
            "color": t["color"],
        }
        for t in _TRACK_DEFS
    }


def _session_path(session_id: str) -> Path:
    safe = Path(session_id).name or "default"
    return STUDIO_STATE_DIR / f"{safe}.json"


def _load_session(session_id: str = "default") -> dict[str, Any]:
    path = _session_path(session_id)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if _is_post_dub_session(session_id):
        built = build_session_from_auto_dub_task(session_id)
        if built:
            return built
    return {
        "session_id": session_id,
        "segments": [],
        "timing_map": [],
        "tracks": _default_tracks(),
        "voice": "ru-RU-DmitryNeural",
        "lang": "ru",
        "plugin_order": ["eq", "compressor"],
        "duration_ms": 120000,
    }


def _save_session(state: dict[str, Any]) -> None:
    sid = str(state.get("session_id") or "default")
    path = _session_path(sid)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    _sync_session_to_auto_dub(sid, state)
    try:
        from engines.project_format import autosave_studio_state

        autosave_studio_state(APP_DIR, state)
    except Exception:
        pass


def _overflow_status(pct: float) -> str:
    if pct <= 5:
        return "green"
    if pct <= 15:
        return "yellow"
    return "red"


def _segment_audio_name(seg: dict[str, Any], *, task_id: str | None = None) -> str | None:
    seg_idx = seg.get("index")
    idx = int(seg_idx) if seg_idx is not None else None
    task_info = _task_info_for(task_id)
    for key in ("fitted_file", "file"):
        name = seg.get(key)
        if not name:
            continue
        from engines.dubbing_engine.session_adapter import resolve_session_audio

        resolved = resolve_session_audio(
            name,
            task_info=task_info,
            default_dir=OUTPUT_DIR,
            segment_index=idx,
        )
        if resolved.is_file():
            return resolved.name
    return None


def _segments_data_from_state(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    """Map studio session segments to auto_dub segments_data + timing_map."""
    task_id = str(state.get("task_id") or state.get("session_id") or "") or None
    segments_data: list[dict[str, Any]] = []
    timing_map: list[dict[str, int]] = []
    for seg in state.get("segments") or []:
        idx = int(seg.get("index", len(segments_data)))
        audio_name = _segment_audio_name(seg, task_id=task_id)
        meta = seg.get("timing_meta") or {}
        start_ms = int(seg.get("start_ms") or 0)
        end_ms = int(seg.get("end_ms") or start_ms + 3000)
        row: dict[str, Any] = {
            "index": idx,
            "text": str(seg.get("text") or "").strip(),
            "file": audio_name,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "allow_atempo": bool(meta.get("atempo", 1.0) and float(meta.get("atempo", 1.0)) > 1.01),
            "place_delay_ms": int(meta.get("delay_ms") or 0),
            "lead_in_ms": int(meta.get("lead_in_ms") or 0),
        }
        if seg.get("fitted_file"):
            row["fitted_file"] = seg.get("fitted_file")
        segments_data.append(row)
        timing_map.append({"start": start_ms, "end": end_ms})
    return segments_data, timing_map


def _studio_preview_path(task_id: str) -> Path:
    safe = Path(task_id).name
    return OUTPUT_DIR / f"studio_preview_{safe}.mp3"


def _session_is_stale_for_preview(task_id: str, preview_path: Path) -> bool:
    session_path = _session_path(task_id)
    if not preview_path.is_file():
        return True
    if not session_path.is_file():
        return False
    try:
        return session_path.stat().st_mtime > preview_path.stat().st_mtime
    except OSError:
        return True


def _render_studio_timed_audio(task_id: str, state: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Build gap-adjusted dub track from current studio segment placements."""
    segments_data, timing_map = _segments_data_from_state(state)
    if timing_map:
        from engines.segment_timing_qa import normalize_timing_map_joints

        timing_map, _joint_fixes = normalize_timing_map_joints(timing_map)
    segment_paths_preview = [
        str(_resolve_task_audio(s.get("file"), task_id=task_id))
        for s in segments_data
        if s.get("file")
    ]
    from engines.dubbing_engine.tts_handoff_diag import (
        log_empty_tts_diagnosis,
        log_track_builder_input,
    )

    log_track_builder_input(
        task_id,
        segment_paths=segment_paths_preview,
        segments_data=segments_data,
        source="studio._render_studio_timed_audio",
    )
    if not any(s.get("file") for s in segments_data):
        task_info = _task_info_for(task_id)
        report = log_empty_tts_diagnosis(
            task_id,
            task_info=task_info,
            segments_data=segments_data,
            segment_paths=segment_paths_preview,
            stage="studio._render_studio_timed_audio",
        )
        reason = str((report or {}).get("reason") or "")
        msg = "Нет TTS-файлов для сборки дорожки"
        if reason:
            msg = f"{msg}: {reason}"
        return None, [msg]

    target_duration = int(state.get("duration_ms") or 0)
    style_params: dict[str, Any] = {}
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, _style_params_from_info

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                style_params = _style_params_from_info(task.get("info"))
                target_duration = int(
                    task.get("info", {}).get("target_duration_ms") or target_duration
                )
    except ImportError:
        pass

    from api.auto_dub_api import _build_timed_dub_track, _safe_export_audio

    timed_audio_obj, warnings, _overlap = _build_timed_dub_track(
        segments_data,
        timing_map,
        target_duration or None,
        task_id,
        style_params=style_params,
    )
    if timed_audio_obj is None:
        return None, ["Не удалось собрать тайминговую дорожку"]

    base_id = task_id[:8]
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                base_id = str(task.get("info", {}).get("mux_base_id") or base_id)
    except ImportError:
        pass

    timed_path = str(_artifacts_dir_for(task_id) / f"{base_id}_timed.mp3")
    if not _safe_export_audio(timed_audio_obj, timed_path):
        return None, ["Ошибка экспорта timed MP3"]

    try:
        from engines.plugins.registry import process_chain

        plugin_order = state.get("plugin_order")
        processed = process_chain(
            timed_path,
            APP_DIR,
            project_order=plugin_order if isinstance(plugin_order, list) else None,
        )
        if processed and Path(processed).is_file() and str(processed) != timed_path:
            import shutil

            shutil.copy2(processed, timed_path)
    except Exception as plug_err:
        logger.warning("studio export plugins skipped task=%s: %s", task_id, plug_err)

    return timed_path, list(warnings or [])


def _resolve_studio_output_path(task_id: str, video_path: str) -> str:
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id) or {}
            info = task.get("info") or {}
            existing = info.get("output_path_full")
            if existing and Path(existing).parent == OUTPUT_DIR:
                return str(existing)
            if task.get("output_file"):
                return str(OUTPUT_DIR / task["output_file"])
            base_id = str(info.get("mux_base_id") or task_id[:8])
    except ImportError:
        base_id = task_id[:8]

    video_stem = Path(video_path).stem
    return str(OUTPUT_DIR / f"{video_stem}_OUTPUT_{base_id}.mp4")


def _existing_mix_output_response(task_id: str) -> dict[str, Any] | None:
    """Return success payload when mix already completed and output MP4 exists."""
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK
    except ImportError:
        return None

    output_name: str | None = None
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task or str(task.get("status") or "") != "done":
            return None
        output_name = task.get("output_file")
        if not output_name:
            info = task.get("info") or {}
            full = info.get("output_path_full")
            if full:
                output_name = Path(str(full)).name
    if not output_name:
        return None

    candidates = [OUTPUT_DIR / str(output_name)]
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                full = (task.get("info") or {}).get("output_path_full")
                if full:
                    candidates.insert(0, Path(str(full)))
    except ImportError:
        pass

    if not any(p.is_file() for p in candidates):
        return None

    return {
        "ok": True,
        "output_file": str(output_name),
        "download": f"/api/dub/download/{output_name}",
        "warnings": [],
        "already_mixed": True,
    }


def _mark_studio_mix_done(
    task_id: str,
    *,
    timed_audio_path: str,
    final_output: str,
    state: dict[str, Any],
    mix_mode_backup: str | None = None,
    keep_assets: bool = True,
) -> None:
    """Update AUTO_TASKS after successful studio mix.

    TTS/session artifacts are kept by default so a follow-up POST /api/studio/mix
    remains idempotent until the user explicitly dismisses the task.
    """
    try:
        from engines.dub_task_state import (
            AUTO_TASKS,
            STATE_LOCK,
            cleanup_task_tts_files,
            touch_task,
        )
    except ImportError:
        return

    output_name = Path(final_output).name
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        info = task.setdefault("info", {})
        info["timed_audio"] = timed_audio_path
        info["output_path_full"] = str(final_output)
        if mix_mode_backup:
            info["mix_mode_backup"] = mix_mode_backup
        seg_data, timing_map = _segments_data_from_state(state)
        info["segments_data"] = seg_data
        info["timing_map_backup"] = timing_map
        info["keep_studio_assets"] = keep_assets
        task["output_file"] = output_name
        task["status"] = "done"
        task["step"] = "dub"
        task["progress"] = 100.0
        if not keep_assets:
            cleanup_task_tts_files(task_id, task, keep_assets=False, output_dir=OUTPUT_DIR)
            try:
                from engines.storage_cleanup import cleanup_after_dub_with_report

                keep = {output_name}
                if timed_audio_path:
                    keep.add(Path(timed_audio_path).name)
                _storage_report = cleanup_after_dub_with_report(
                    APP_DIR,
                    OUTPUT_DIR,
                    Path(info.get("session_dir") or ""),
                    keep_names=keep,
                )
                info["storage_cleanup"] = _storage_report.to_dict()
                info["storage_report"] = _storage_report.to_dict()
            except Exception as cleanup_err:
                logger.debug("studio mix cleanup skipped task=%s: %s", task_id, cleanup_err)
        touch_task(task_id)


def _remux_studio_mp4(task_id: str, timed_audio_path: str, state: dict[str, Any]) -> tuple[bool, str | None, list[str]]:
    """Remux video with assembled dub track (full_dub)."""
    video_path = state.get("video_path") or ""
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.get("info") or {}
                video_path = info.get("video_path_backup") or video_path
    except ImportError:
        pass

    if not video_path or not Path(video_path).is_file():
        return False, None, ["Исходное видео недоступно для экспорта"]

    output_path = _resolve_studio_output_path(task_id, video_path)
    target_duration = int(state.get("duration_ms") or 0)
    dub_timeout_sec = max(600, int(target_duration / 1000) + 300)

    from engines.dub_engine import DubEngine
    from engines.source_separation import (
        build_final_mix_diagnostics,
        get_background_mix_params,
    )

    bg_path: str | None = None
    bg_atten_db = 4.5
    sep_info: dict[str, Any] = {}
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.get("info") or {}
                video_path = info.get("video_path_backup") or video_path
                sep_info = dict(info.get("source_separation") or {})
    except ImportError:
        pass

    bg_path, bg_atten_db, _sep_ok = get_background_mix_params(
        {"source_separation": sep_info}
    )

    ok, out_path, dub_errors = DubEngine(
        video_path=video_path,
        timed_audio=timed_audio_path,
        background_audio_path=bg_path or "",
        background_attenuation_db=bg_atten_db,
    ).run(
        output_path=output_path,
        mix_mode="full_dub",
        timeout_sec=dub_timeout_sec,
    )
    if not ok:
        errs = dub_errors if isinstance(dub_errors, list) else [str(dub_errors)]
        return False, None, errs

    final_output = out_path or output_path
    if not final_output or not Path(final_output).exists():
        return False, None, ["MP4 не создан после remux"]

    used_stem_mix = bool(bg_path)
    mix_diag = build_final_mix_diagnostics(
        separation_info=sep_info,
        final_mp4_path=str(final_output),
        mix_success=True,
        used_stem_mix=used_stem_mix,
    )
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                task.setdefault("info", {})["source_separation_final_mix"] = mix_diag.to_dict()
    except ImportError:
        pass

    output_name = Path(final_output).name
    _mark_studio_mix_done(
        task_id,
        timed_audio_path=timed_audio_path,
        final_output=str(final_output),
        state=state,
        mix_mode_backup="full_dub",
    )

    return True, str(final_output), []


def export_studio_task(task_id: str, *, remux: bool = True) -> dict[str, Any]:
    """Build timed track from studio session; optionally remux final MP4."""
    safe = Path(task_id).name
    blocked = _studio_access(safe)
    if blocked:
        return {"ok": False, "error": blocked}

    with _LOCK:
        state = _load_session(safe)
        if not state.get("segments"):
            return {"ok": False, "error": "Сессия Studio пуста"}

        timed_path, warnings = _render_studio_timed_audio(safe, state)
        if not timed_path:
            return {"ok": False, "error": (warnings or ["timed audio failed"])[0]}

        preview_path = _studio_preview_path(safe)
        try:
            import shutil

            shutil.copy2(timed_path, preview_path)
        except OSError:
            pass

        result: dict[str, Any] = {
            "ok": True,
            "timed_audio": Path(timed_path).name,
            "preview_url": f"/api/studio/preview/{safe}",
            "warnings": warnings[:10],
        }

        if remux:
            mux_ok, out_path, mux_errors = _remux_studio_mp4(safe, timed_path, state)
            if not mux_ok:
                result["ok"] = False
                result["error"] = (mux_errors or ["remux failed"])[0]
                result["remux_errors"] = mux_errors
                return result
            result["output_file"] = Path(out_path).name
            result["download"] = f"/api/dub/download/{Path(out_path).name}"

        return result


def _enrich_segment(seg: dict[str, Any], timing_map: list[Any]) -> dict[str, Any]:
    idx = int(seg.get("index", seg.get("id", 0)))
    if seg.get("start_ms") is None and idx < len(timing_map):
        tm = timing_map[idx]
        if isinstance(tm, dict):
            seg["start_ms"] = int(tm.get("start", tm.get("start_ms", 0)))
            seg["end_ms"] = int(tm.get("end", tm.get("end_ms", 0)))
        elif isinstance(tm, (list, tuple)) and len(tm) >= 2:
            seg["start_ms"], seg["end_ms"] = int(tm[0]), int(tm[1])
    slot = max(1, int(seg.get("end_ms", 0)) - int(seg.get("start_ms", 0)))
    fitted_ms = int(seg.get("fitted_ms") or 0)
    tts_ms = int(seg.get("tts_ms") or 0)
    visual_ms = fitted_ms or tts_ms
    try:
        from engines.timing_fit import DUB_SLOT_TOLERANCE_MS as _TOL
    except Exception:
        _TOL = 75
    overflow_ms = max(0, visual_ms - (slot + _TOL))
    pct = round(100.0 * overflow_ms / slot, 1)
    seg["overflow_pct"] = pct
    seg["overflow_ms"] = overflow_ms
    seg["container_status"] = _overflow_status(pct)
    if not seg.get("tts_ms") and visual_ms:
        seg["tts_ms"] = visual_ms
    if not seg.get("fitted_ms") and visual_ms:
        seg["fitted_ms"] = visual_ms
    seg.setdefault("id", str(idx))
    return seg


def _segments_response(segments: list[SubtitleSegment], title: str = "") -> dict:
    return {
        "ok": True,
        "title": title,
        "segments": [s.to_dict() for s in segments],
        "text": segments_to_text(segments),
        "timing_map": segments_to_timing_map(segments),
        "lines": len(segments),
    }


def _find_segment(segments: list[dict[str, Any]], sid: str) -> tuple[dict[str, Any] | None, int]:
    seg = next((s for s in segments if str(s.get("id", s.get("index"))) == sid), None)
    if seg is not None:
        try:
            return seg, int(seg.get("index", 0))
        except Exception:
            return seg, segments.index(seg)
    try:
        idx = int(sid)
        if 0 <= idx < len(segments):
            return segments[idx], idx
    except ValueError:
        pass
    return None, -1


def _reindex_segments(segments: list[dict[str, Any]], timing: list[Any]) -> None:
    while len(timing) < len(segments):
        timing.append({"start": 0, "end": 0})
    if len(timing) > len(segments):
        del timing[len(segments):]
    for i, seg in enumerate(segments):
        seg["index"] = i
        seg["id"] = str(i)
        seg["start_ms"] = int(seg.get("start_ms") or 0)
        seg["end_ms"] = int(seg.get("end_ms") or seg["start_ms"] + 3000)
        timing[i] = {"start": seg["start_ms"], "end": seg["end_ms"]}
        _enrich_segment(seg, timing)


@bp.post("/api/studio/import")
def api_studio_import():
    """Import subtitle file (SRT/VTT/ASS/SSA/TXT) or raw text."""
    if "file" in request.files:
        file = request.files["file"]
        filename = file.filename or "subs.srt"
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED:
            return jsonify({"error": f"Формат {ext} не поддерживается"}), 400
        raw = file.read().decode("utf-8", errors="replace")
        segments = parse_subtitles(raw, ext)
        if not segments:
            return jsonify({"error": "Субтитры не распознаны или файл пуст"}), 400
        return jsonify(_segments_response(segments, Path(filename).stem))

    data = request.get_json(silent=True) or {}
    raw = (data.get("text") or "").strip()
    ext = (data.get("ext") or ".srt").lower()
    if not raw:
        return jsonify({"error": "Нет данных для импорта"}), 400
    segments = parse_subtitles(raw, ext)
    if not segments:
        return jsonify({"error": "Не удалось разобрать субтитры"}), 400
    return jsonify(_segments_response(segments))


@bp.post("/api/studio/export")
def api_studio_export():
    """Export segments to SRT or VTT."""
    data = request.get_json(silent=True) or {}
    fmt = (data.get("format") or "srt").lower()
    items = data.get("segments") or []
    if not items:
        return jsonify({"error": "Нет сегментов для экспорта"}), 400

    segments = segments_from_payload(items)
    if not segments:
        return jsonify({"error": "Сегменты пусты"}), 400

    if fmt == "vtt":
        content = export_vtt(segments)
        ext = ".vtt"
        mime = "text/vtt"
    else:
        content = export_srt(segments)
        ext = ".srt"
        mime = "text/plain"

    name = (data.get("name") or "subtitles").strip() or "subtitles"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    filename = f"{safe}_{uuid.uuid4().hex[:6]}{ext}"
    path = OUTPUT_DIR / filename
    path.write_text(content, encoding="utf-8")

    return jsonify(
        {
            "ok": True,
            "filename": filename,
            "download": f"/api/studio/download/{filename}",
            "format": fmt,
            "mime": mime,
        }
    )


@bp.get("/api/studio/download/<filename>")
def api_studio_download(filename):
    safe = Path(filename).name
    path = OUTPUT_DIR / safe
    if not path.exists() or path.suffix.lower() not in (".srt", ".vtt"):
        abort(404)
    mime = "text/vtt" if safe.endswith(".vtt") else "text/plain"
    return send_file(str(path), as_attachment=True, download_name=safe, mimetype=mime)


@bp.post("/api/studio/prepare_redub")
def api_studio_prepare_redub():
    """Save studio segments for dub page (skip STT when present)."""
    data = request.get_json(silent=True) or {}
    source = (data.get("source_text") or "").strip()
    translated = (data.get("translated_text") or "").strip()
    segments = data.get("segments") or []
    timing_map = data.get("timing_map") or []

    if segments:
        seg_objs = segments_from_payload(segments)
        source_lines = [s.text for s in seg_objs]
        timing_map = segments_to_timing_map(seg_objs)
    elif source:
        source_lines = [ln.strip() for ln in source.splitlines() if ln.strip()]
    else:
        return jsonify({"error": "Нет текста для дубляжа"}), 400

    translated_lines = (
        [ln.strip() for ln in translated.splitlines() if ln.strip()]
        if translated
        else []
    )

    explicit_skip = bool(data.get("skip_translate"))
    skip_translate = explicit_skip and bool(translated_lines)

    redub_id = uuid.uuid4().hex[:12]
    payload = {
        "source_segments": source_lines,
        "timing_map": timing_map,
    }
    if skip_translate:
        payload["translated_segments"] = translated_lines
        payload["skip_translate"] = True
    path = REDBUB_DIR / f"{redub_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return jsonify({"ok": True, "redub_id": redub_id, "route": f"/dub?redub={redub_id}"})


@bp.get("/api/studio/redub/<redub_id>")
def api_studio_redub_get(redub_id):
    safe = Path(redub_id).name
    path = REDBUB_DIR / f"{safe}.json"
    if not path.exists():
        return jsonify({"error": "Пакет redub не найден"}), 404
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, **payload})


@bp.get("/api/studio/tracks")
def api_studio_tracks():
    session_id = request.args.get("session", "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    state = _load_session(session_id)
    return jsonify({
        "ok": True,
        "tracks": state.get("tracks") or _default_tracks(),
        "track_defs": _TRACK_DEFS,
        "duration_ms": state.get("duration_ms", 120000),
    })


@bp.get("/api/studio/segments")
def api_studio_segments():
    session_id = request.args.get("session", "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    state = _load_session(session_id)
    timing = state.get("timing_map") or []
    segments = [
        _enrich_segment(dict(s), timing)
        for s in (state.get("segments") or [])
    ]
    return jsonify({
        "ok": True,
        "segments": segments,
        "timing_map": timing,
        "duration_ms": state.get("duration_ms", 120000),
        "source_segments": state.get("source_segments") or [],
    })


@bp.post("/api/studio/segments/sync")
def api_studio_segments_sync():
    """Sync segments from client (autosave)."""
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    with _LOCK:
        state = _load_session(session_id)
        if data.get("segments") is not None:
            state["segments"] = data["segments"]
        if data.get("timing_map") is not None:
            state["timing_map"] = data["timing_map"]
        if data.get("source_segments") is not None:
            state["source_segments"] = data["source_segments"]
        if data.get("duration_ms"):
            state["duration_ms"] = int(data["duration_ms"])
        if data.get("voice"):
            state["voice"] = data["voice"]
        _save_session(state)
    return jsonify({"ok": True, "saved": True})


@bp.post("/api/studio/track/<track_id>/state")
def api_studio_track_state(track_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    tid = Path(track_id).name
    with _LOCK:
        state = _load_session(session_id)
        tracks = state.setdefault("tracks", _default_tracks())
        if tid not in tracks:
            return jsonify({"ok": False, "error": "Unknown track"}), 400
        if "muted" in data:
            tracks[tid]["muted"] = bool(data["muted"])
        if "solo" in data:
            tracks[tid]["solo"] = bool(data["solo"])
            if tracks[tid]["solo"]:
                for k in tracks:
                    if k != tid:
                        tracks[k]["solo"] = False
        if "volume" in data:
            tracks[tid]["volume"] = float(data["volume"])
        _save_session(state)
    return jsonify({"ok": True, "track_id": tid, "state": tracks[tid]})


@bp.post("/api/studio/track/<track_id>/mute")
def api_studio_track_mute(track_id: str):
    return api_studio_track_state(track_id)


@bp.post("/api/studio/track/<track_id>/solo")
def api_studio_track_state_solo(track_id: str):
    return api_studio_track_state(track_id)


@bp.post("/api/studio/track/<track_id>/volume")
def api_studio_track_volume(track_id: str):
    return api_studio_track_state(track_id)


@bp.post("/api/studio/segment/<segment_id>/regenerate")
def api_studio_segment_regenerate(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg = next((s for s in segments if str(s.get("id", s.get("index"))) == sid), None)
        if seg is None:
            try:
                idx = int(sid)
                if 0 <= idx < len(segments):
                    seg = segments[idx]
            except ValueError:
                pass
        if seg is None:
            return jsonify({"ok": False, "error": "segment not found"}), 404

        if data.get("text"):
            seg["text"] = data["text"]
        voice = str(data.get("voice") or state.get("voice") or "ru-RU-DmitryNeural")
        lang = str(data.get("lang") or state.get("lang") or "ru")
        src_idx = int(seg.get("index", sid))
        source_hint = ""
        src_segs = state.get("source_segments") or []
        if src_idx < len(src_segs):
            source_hint = str(src_segs[src_idx])

        from engines.regeneration import regenerate_segment

        result = regenerate_segment(
            seg,
            timing_map=timing,
            voice=voice,
            lang=lang,
            source_hint=source_hint,
            app_dir=APP_DIR,
            use_soft_sync=data.get("use_soft_sync"),
        )
        _enrich_segment(seg, timing)
        _save_session(state)
    return jsonify(result)


@bp.post("/api/studio/segment/<segment_id>/auto-fix")
def api_studio_segment_auto_fix(segment_id: str):
    data = request.get_json(silent=True) or {}
    data["use_soft_sync"] = True
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg = next((s for s in segments if str(s.get("id", s.get("index"))) == sid), None)
        if seg is None:
            try:
                idx = int(sid)
                if 0 <= idx < len(segments):
                    seg = segments[idx]
            except ValueError:
                pass
        if seg is None:
            return jsonify({"ok": False, "error": "segment not found"}), 404
        voice = str(data.get("voice") or state.get("voice") or "ru-RU-DmitryNeural")
        lang = str(data.get("lang") or state.get("lang") or "ru")
        src_idx = int(seg.get("index", sid))
        source_hint = ""
        src_segs = state.get("source_segments") or []
        if src_idx < len(src_segs):
            source_hint = str(src_segs[src_idx])
        from engines.regeneration import auto_fix_segment

        result = auto_fix_segment(
            seg,
            timing_map=timing,
            voice=voice,
            lang=lang,
            source_hint=source_hint,
            app_dir=APP_DIR,
        )
        _enrich_segment(seg, timing)
        _save_session(state)
    return jsonify(result)


@bp.patch("/api/studio/segment/<segment_id>/emotion")
def api_studio_segment_emotion(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    emotion = str(data.get("emotion") or "neutral")
    regenerate = bool(data.get("regenerate", True))
    sid = str(segment_id)
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        seg = next((s for s in segments if str(s.get("id", s.get("index"))) == sid), None)
        if seg is None:
            try:
                idx = int(sid)
                if 0 <= idx < len(segments):
                    seg = segments[idx]
            except ValueError:
                pass
        if seg is None:
            return jsonify({"ok": False, "error": "segment not found"}), 404

        from engines.emotion_tagger import apply_emotion_to_segment

        apply_emotion_to_segment(seg, emotion)
        _save_session(state)

        if regenerate:
            voice = str(data.get("voice") or state.get("voice") or "ru-RU-DmitryNeural")
            lang = str(data.get("lang") or state.get("lang") or "ru")
            timing = state.get("timing_map") or []
            from engines.regeneration import regenerate_segment

            result = regenerate_segment(
                seg,
                timing_map=timing,
                voice=voice,
                lang=lang,
                app_dir=APP_DIR,
            )
            _enrich_segment(seg, timing)
            _save_session(state)
            return jsonify(result)
    return jsonify({"ok": True, "segment": seg})


@bp.get("/api/studio/plugins")
def api_studio_plugins():
    session_id = request.args.get("session", "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    from engines.plugins.registry import list_plugins, load_order

    state = _load_session(session_id)
    return jsonify({
        "ok": True,
        "plugins": list_plugins(),
        "order": load_order(APP_DIR, project_order=state.get("plugin_order")),
    })


@bp.post("/api/studio/plugins/order")
def api_studio_plugins_order():
    data = request.get_json(silent=True) or {}
    order = data.get("order") or []
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    with _LOCK:
        state = _load_session(session_id)
        state["plugin_order"] = [str(x) for x in order]
        _save_session(state)
        from engines.plugins.registry import save_order

        save_order(APP_DIR, state["plugin_order"], project_id=session_id)
    return jsonify({"ok": True, "order": state["plugin_order"]})


@bp.get("/api/studio/session/<task_id>")
def api_studio_session(task_id: str):
    """Load studio session from auto_dub task (production post-dub flow)."""
    safe = Path(task_id).name
    state = _load_session(safe)
    if not state.get("segments") and not _is_post_dub_session(safe):
        built = build_session_from_auto_dub_task(safe)
        if built:
            state = built
            _save_session(state)
    if not state.get("segments"):
        return jsonify({"ok": False, "error": "Сессия не найдена"}), 404

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(safe)
            if task:
                state["task_status"] = task.get("status")
                state["output_file"] = task.get("output_file")
                info = task.get("info") or {}
                state["studio_url"] = info.get("studio_url") or state.get("studio_url")
    except ImportError:
        pass

    timing = state.get("timing_map") or []
    segments = [_enrich_segment(dict(s), timing) for s in (state.get("segments") or [])]
    from engines.locale_utils import locale_from_request

    return jsonify({
        "ok": True,
        "ui_lang": locale_from_request(),
        "session": {
            **state,
            "segments": segments,
        },
    })


@bp.post("/api/studio/segment/<segment_id>/move")
def api_studio_segment_move(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)
    start_ms = data.get("start_ms")
    end_ms = data.get("end_ms")
    if start_ms is None and end_ms is None:
        return jsonify({"ok": False, "error": "start_ms or end_ms required"}), 400

    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg = next((s for s in segments if str(s.get("id", s.get("index"))) == sid), None)
        if seg is None:
            try:
                idx = int(sid)
                if 0 <= idx < len(segments):
                    seg = segments[idx]
            except ValueError:
                pass
        if seg is None:
            return jsonify({"ok": False, "error": "segment not found"}), 404

        if start_ms is not None:
            seg["start_ms"] = int(start_ms)
        if end_ms is not None:
            seg["end_ms"] = int(end_ms)
        idx = int(seg.get("index", sid))
        while len(timing) <= idx:
            timing.append({"start": 0, "end": 0})
        timing[idx] = {"start": seg["start_ms"], "end": seg["end_ms"]}
        state["timing_map"] = timing
        _enrich_segment(seg, timing)
        _save_session(state)
    return jsonify({"ok": True, "segment": seg})


@bp.post("/api/studio/segment/<segment_id>/split")
def api_studio_segment_split(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg, idx = _find_segment(segments, sid)
        if seg is None or idx < 0:
            return jsonify({"ok": False, "error": "segment not found"}), 404
        start_ms = int(seg.get("start_ms") or 0)
        end_ms = int(seg.get("end_ms") or start_ms + 3000)
        split_at = int(data.get("at_ms") or (start_ms + end_ms) // 2)
        split_at = max(start_ms + 150, min(end_ms - 150, split_at))
        if split_at <= start_ms or split_at >= end_ms:
            return jsonify({"ok": False, "error": "invalid split point"}), 400

        words = str(seg.get("text") or "").split()
        mid = max(1, len(words) // 2)
        left_text = " ".join(words[:mid]).strip() or str(seg.get("text") or "")
        right_text = " ".join(words[mid:]).strip() or left_text
        seg["text"] = left_text
        seg["end_ms"] = split_at
        seg["file"] = None
        seg["fitted_file"] = None
        right = dict(seg)
        right["text"] = right_text
        right["start_ms"] = split_at
        right["end_ms"] = end_ms
        right["file"] = None
        right["fitted_file"] = None
        right["overflow_pct"] = 0.0
        right["overflow_ms"] = 0
        right["container_status"] = "green"
        segments.insert(idx + 1, right)
        _reindex_segments(segments, timing)
        state["segments"] = segments
        state["timing_map"] = timing
        _save_session(state)
    return jsonify({"ok": True, "segments": segments, "segment": segments[idx], "split": segments[idx + 1]})


@bp.post("/api/studio/segment/<segment_id>/copy")
def api_studio_segment_copy(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg, idx = _find_segment(segments, sid)
        if seg is None or idx < 0:
            return jsonify({"ok": False, "error": "segment not found"}), 404
        dup = dict(seg)
        dup["file"] = None
        dup["fitted_file"] = None
        dup["overflow_pct"] = 0.0
        dup["overflow_ms"] = 0
        dup["container_status"] = "green"
        segments.insert(idx + 1, dup)
        _reindex_segments(segments, timing)
        state["segments"] = segments
        state["timing_map"] = timing
        _save_session(state)
    return jsonify({"ok": True, "segments": segments, "segment": segments[idx + 1]})


@bp.post("/api/studio/segment/<segment_id>/delete")
def api_studio_segment_delete(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg, idx = _find_segment(segments, sid)
        if seg is None or idx < 0:
            return jsonify({"ok": False, "error": "segment not found"}), 404
        segments.pop(idx)
        _reindex_segments(segments, timing)
        state["segments"] = segments
        state["timing_map"] = timing
        _save_session(state)
    return jsonify({"ok": True, "segments": segments})


@bp.post("/api/studio/segments/merge")
def api_studio_segments_merge():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    merge_ids = [str(x) for x in (data.get("segment_ids") or [])]
    if len(merge_ids) < 2:
        return jsonify({"ok": False, "error": "need 2+ segments"}), 400
    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        targets = []
        for sid in merge_ids:
            seg, idx = _find_segment(segments, sid)
            if seg is not None and idx >= 0:
                targets.append((idx, seg))
        if len(targets) < 2:
            return jsonify({"ok": False, "error": "segments not found"}), 404
        targets.sort(key=lambda x: x[0])
        first_idx = targets[0][0]
        first = targets[0][1]
        merged_text = " ".join(str(s.get("text") or "").strip() for _, s in targets).strip()
        first["text"] = merged_text
        first["start_ms"] = min(int(s.get("start_ms") or 0) for _, s in targets)
        first["end_ms"] = max(int(s.get("end_ms") or 0) for _, s in targets)
        first["file"] = None
        first["fitted_file"] = None
        keep = {id(s) for _, s in targets[1:]}
        segments = [s for s in segments if id(s) not in keep]
        _reindex_segments(segments, timing)
        state["segments"] = segments
        state["timing_map"] = timing
        _save_session(state)
    return jsonify({"ok": True, "segments": segments, "segment": segments[first_idx]})


@bp.post("/api/studio/segment/<segment_id>/time-stretch")
def api_studio_segment_time_stretch(segment_id: str):
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "default")
    blocked = _studio_access(session_id)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    sid = str(segment_id)

    with _LOCK:
        state = _load_session(session_id)
        segments = state.get("segments") or []
        timing = state.get("timing_map") or []
        seg = next((s for s in segments if str(s.get("id", s.get("index"))) == sid), None)
        if seg is None:
            try:
                idx = int(sid)
                if 0 <= idx < len(segments):
                    seg = segments[idx]
            except ValueError:
                pass
        if seg is None:
            return jsonify({"ok": False, "error": "segment not found"}), 404

        tts_file = seg.get("file")
        if not tts_file:
            return jsonify({"ok": False, "error": "no_tts_file"}), 400

        tts_path = _resolve_task_audio(tts_file, task_id=safe)
        if not tts_path.is_file():
            return jsonify({"ok": False, "error": "tts_file_missing"}), 404

        start_ms = int(seg.get("start_ms") or 0)
        end_ms = int(seg.get("end_ms") or start_ms + 3000)
        import tempfile
        import shutil

        from engines.timing_fit import fit_segment_audio

        work = Path(tempfile.mkdtemp(prefix="studio_stretch_"))
        fitted_path, meta = fit_segment_audio(
            tts_path,
            start_ms,
            end_ms,
            work_dir=work,
            allow_atempo=True,
        )
        fitted_name = f"stretch_{sid}_{uuid.uuid4().hex[:8]}.wav"
        shutil.copy2(fitted_path, OUTPUT_DIR / fitted_name)
        tts_ms = _audio_duration_ms(tts_path)
        fitted_ms = _audio_duration_ms(Path(fitted_path))
        slot_ms = max(1, end_ms - start_ms)
        overflow_ms = max(0, fitted_ms - slot_ms)
        overflow_pct = round(100.0 * overflow_ms / slot_ms, 1)
        seg["fitted_file"] = fitted_name
        seg["tts_ms"] = tts_ms
        seg["fitted_ms"] = fitted_ms
        seg["overflow_ms"] = overflow_ms
        seg["overflow_pct"] = overflow_pct
        seg["container_status"] = _overflow_status(overflow_pct)
        seg["timing_meta"] = meta or {}
        _save_session(state)
    return jsonify({"ok": True, "segment": seg, "overflow_pct": overflow_pct})


@bp.post("/api/studio/export/<task_id>")
def api_studio_export_mp4(task_id: str):
    """Export MP4: timing_fit track + full_dub remux from current studio placements."""
    safe = Path(task_id).name
    data = request.get_json(silent=True) or {}
    if data.get("segments"):
        with _LOCK:
            state = _load_session(safe)
            state["segments"] = data["segments"]
            if data.get("timing_map"):
                state["timing_map"] = data["timing_map"]
            _save_session(state)

    remux = data.get("remux", True)
    if isinstance(remux, str):
        remux = remux.strip().lower() not in ("0", "false", "no")
    result = export_studio_task(safe, remux=bool(remux))
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


def _mix_studio_mp4_with_task_settings(
    task_id: str,
    timed_audio_path: str,
    state: dict[str, Any],
    *,
    keep_assets: bool = True,
) -> tuple[bool, str | None, list[str]]:
    """Remux MP4 using the original pipeline DubEngine settings stored in AUTO_TASKS."""
    video_path = state.get("video_path") or ""
    mix_mode = "full_dub"
    mix_volume = None
    dub_mode = None
    original_volume = None
    dub_volume = None
    background_volume = None
    target_duration = int(state.get("duration_ms") or 0)
    video_stretch_segs: list[dict[str, Any]] = []

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK  # type: ignore[attr-defined]

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.get("info") or {}
                video_path = info.get("video_path_backup") or video_path
                mix_mode = info.get("mix_mode_backup") or mix_mode
                mix_volume = info.get("mix_volume_backup")
                dub_mode = info.get("dub_mode_backup")
                mix_vols = info.get("mix_volumes_backup") or {}
                original_volume = mix_vols.get("original_volume")
                dub_volume = mix_vols.get("dub_volume")
                background_volume = mix_vols.get("background_volume")
                target_duration = int(info.get("target_duration_ms") or target_duration)
                video_stretch_segs = list(info.get("video_stretch_segments") or [])
    except ImportError:
        pass

    if not video_path or not Path(video_path).is_file():
        return False, None, ["Исходное видео недоступно для микширования"]

    output_path = _resolve_studio_output_path(task_id, video_path)
    dub_timeout_sec = max(600, int(target_duration / 1000) + 300)

    from engines.dub_engine import DubEngine
    from engines.source_separation import (
        build_final_mix_diagnostics,
        get_background_mix_params,
    )

    bg_path: str | None = None
    bg_atten_db = 4.5
    sep_info: dict[str, Any] = {}
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK  # type: ignore[attr-defined]

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                sep_info = dict((task.get("info") or {}).get("source_separation") or {})
    except ImportError:
        pass

    bg_path, bg_atten_db, _sep_ok = get_background_mix_params(
        {"source_separation": sep_info}
    )

    ok, out_path, dub_errors = DubEngine(
        video_path=video_path,
        timed_audio=timed_audio_path,
        video_stretch_segments=video_stretch_segs,
        background_audio_path=bg_path or "",
        background_attenuation_db=bg_atten_db,
    ).run(
        output_path=output_path,
        mode=dub_mode,
        mix_mode=mix_mode,
        mix_volume=mix_volume,
        original_volume=original_volume,
        dub_volume=dub_volume,
        background_volume=background_volume,
        timeout_sec=dub_timeout_sec,
    )
    if not ok:
        errs = dub_errors if isinstance(dub_errors, list) else [str(dub_errors)]
        return False, None, errs

    final_output = out_path or output_path
    if not final_output or not Path(final_output).exists():
        return False, None, ["MP4 не создан после микширования"]

    used_stem_mix = bool(bg_path)
    mix_diag = build_final_mix_diagnostics(
        separation_info=sep_info,
        final_mp4_path=str(final_output),
        mix_success=True,
        used_stem_mix=used_stem_mix,
    )
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK  # type: ignore[attr-defined]

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                task.setdefault("info", {})["source_separation_final_mix"] = mix_diag.to_dict()
    except ImportError:
        pass

    _mark_studio_mix_done(
        task_id,
        timed_audio_path=timed_audio_path,
        final_output=str(final_output),
        state=state,
        keep_assets=keep_assets,
    )

    return True, str(final_output), []


def run_studio_mix_internal(
    task_id: str,
    *,
    force: bool = True,
    keep_assets: bool = True,
) -> tuple[bool, str | None, list[str]]:
    """Run final studio mix outside HTTP context.

    Returns (ok, output_filename, errors).
    """
    safe = Path(task_id).name
    existing = _existing_mix_output_response(safe)
    if existing:
        return True, existing.get("output_file"), list(existing.get("warnings") or [])

    with _LOCK:
        state = _load_session(safe)
        if not state.get("segments"):
            return False, None, ["Сессия Studio пуста"]

        segments = state.get("segments") or []
        red_segs = [
            s
            for s in segments
            if (
                s.get("container_status") == "red"
                or float(s.get("overflow_pct") or 0) > 15
            )
        ]
        if red_segs and not force:
            return False, None, [f"Есть {len(red_segs)} переполненных сегментов"]

        timed_path, warnings = _render_studio_timed_audio(safe, state)
        if not timed_path:
            err = (warnings or ["Не удалось собрать тайминговую дорожку"])[0]
            return False, None, [err]

        preview_path = _studio_preview_path(safe)
        try:
            import shutil

            shutil.copy2(timed_path, preview_path)
        except OSError:
            pass

        mux_ok, out_path, mux_errors = _mix_studio_mp4_with_task_settings(
            safe,
            timed_path,
            state,
            keep_assets=keep_assets,
        )
        if not mux_ok:
            return False, None, mux_errors or ["Ошибка ремукса MP4"]

        output_name = Path(out_path).name  # type: ignore[arg-type]
        state["output_file"] = output_name
        _save_session(state)

    try:
        from engines.open_ddf import open_ddf as _oddf

        _oddf.record_agent(
            safe, "Mix", called=True, success=True, decision=f"output={output_name}"
        )
        _oddf.save(safe)
    except Exception:
        pass

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK  # type: ignore[attr-defined]

        with STATE_LOCK:
            task = AUTO_TASKS.get(safe)
            if task:
                task["status"] = "done"
                task["step"] = "done"
                task["progress"] = 100.0
                task["output_file"] = output_name
                task.setdefault("info", {})["studio_mix_auto"] = True
    except ImportError:
        pass

    return True, output_name, warnings[:10]


@bp.post("/api/studio/mix/<task_id>")
def api_studio_mix(task_id: str):
    """Final project mix: build timed track + remux MP4.

    Body (JSON, optional):
        force (bool): если true — свести даже при наличии красных сегментов.

    Returns:
        200 + {ok, output_file, download, warnings} — успешно
        409 + {ok:false, error_code:'overflow_segments', overflow_count, overflow_segments} — есть
              переполненные сегменты и force не передан
        400/404 + {ok:false, error} — другие ошибки
    """
    safe = Path(task_id).name
    blocked = _studio_access(safe)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403

    existing = _existing_mix_output_response(safe)
    if existing:
        return jsonify(existing), 200

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))

    with _LOCK:
        state = _load_session(safe)
        if not state.get("segments"):
            return jsonify({"ok": False, "error": "Сессия Studio пуста"}), 404

        segments = state.get("segments") or []

        # Проверяем переполненные (красные) сегменты
        red_segs = [
            {
                "id": s.get("id"),
                "index": s.get("index"),
                "overflow_pct": round(float(s.get("overflow_pct") or 0), 1),
                "text": str(s.get("text") or "")[:60],
            }
            for s in segments
            if (
                s.get("container_status") == "red"
                or float(s.get("overflow_pct") or 0) > 15
            )
        ]

        if red_segs and not force:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error_code": "overflow_segments",
                        "overflow_count": len(red_segs),
                        "overflow_segments": red_segs,
                        "error": f"Есть {len(red_segs)} переполненных сегментов. "
                        "Исправьте их или передайте force=true для принудительного сведения.",
                    }
                ),
                409,
            )

        # Строим тайминговую дорожку
        timed_path, warnings = _render_studio_timed_audio(safe, state)
        if not timed_path:
            err = (warnings or ["Не удалось собрать тайминговую дорожку"])[0]
            return jsonify({"ok": False, "error": err}), 400

        # Обновляем preview
        preview_path = _studio_preview_path(safe)
        try:
            import shutil

            shutil.copy2(timed_path, preview_path)
        except OSError:
            pass

        # Ремукс MP4 с настройками оригинального пайплайна
        mux_ok, out_path, mux_errors = _mix_studio_mp4_with_task_settings(
            safe, timed_path, state
        )
        if not mux_ok:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": (mux_errors or ["Ошибка ремукса MP4"])[0],
                        "remux_errors": mux_errors,
                    }
                ),
                400,
            )

        output_name = Path(out_path).name  # type: ignore[arg-type]
        download_url = f"/api/dub/download/{output_name}"

        # Обновляем сессию studio с новым output_file
        state["output_file"] = output_name
        _save_session(state)

        # Record successful Mix to OpenDDF
        try:
            from engines.open_ddf import open_ddf as _oddf

            _oddf.record_agent(
                safe, "Mix", called=True, success=True,
                decision=f"output={output_name}",
            )
            _oddf.save(safe)
        except Exception:
            pass

        ddf_url = f"/api/ddf/{safe}"
        try:
            from engines.open_ddf import open_ddf as _oddf2

            _ddf_rep = _oddf2.get_report(safe)
            ddf_warnings = _ddf_rep.get("summary", {}).get("warnings", 0)
        except Exception:
            ddf_warnings = 0

        return jsonify(
            {
                "ok": True,
                "output_file": output_name,
                "download": download_url,
                "warnings": warnings[:10],
                "ddf_url": ddf_url,
                "ddf_warnings": ddf_warnings,
            }
        )


@bp.get("/api/studio/video/<task_id>")
def api_studio_video(task_id: str):
    """Serve source video for post-dub studio preview."""
    safe = Path(task_id).name
    state = _load_session(safe)
    video_path = state.get("video_path") or ""
    if not video_path:
        try:
            from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

            with STATE_LOCK:
                task = AUTO_TASKS.get(safe)
                if task:
                    video_path = (task.get("info") or {}).get("video_path_backup") or ""
        except ImportError:
            pass
    if not video_path:
        abort(404)
    path = Path(str(video_path))
    if not path.is_file():
        preview = state.get("video_preview")
        if preview:
            path = UPLOADS_DIR / Path(str(preview)).name
    if not path.is_file():
        abort(404)
    ext = path.suffix.lower().lstrip(".")
    mime_map = {
        "mp4": "video/mp4",
        "mkv": "video/x-matroska",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "webm": "video/webm",
    }
    return send_file(
        str(path),
        mimetype=mime_map.get(ext, "video/mp4"),
        conditional=True,
        download_name=path.name,
    )


@bp.get("/api/studio/preview/<task_id>")
def api_studio_preview(task_id: str):
    """Serve assembled TTS preview MP3 for studio playback sync."""
    safe = Path(task_id).name
    blocked = _studio_access(safe)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403

    preview_path = _studio_preview_path(safe)
    refresh = request.args.get("refresh", "").strip().lower() in ("1", "true", "yes")

    with _LOCK:
        state = _load_session(safe)
        if not state.get("segments"):
            return jsonify({"ok": False, "error": "Сессия не найдена"}), 404

        if refresh or _session_is_stale_for_preview(safe, preview_path):
            timed_path, warnings = _render_studio_timed_audio(safe, state)
            if not timed_path:
                err = (warnings or ["preview build failed"])[0]
                return jsonify({"ok": False, "error": err}), 400
            try:
                import shutil

                shutil.copy2(timed_path, preview_path)
            except OSError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 500

    if not preview_path.is_file():
        return jsonify({"ok": False, "error": "Preview недоступен"}), 404

    return send_file(
        str(preview_path),
        mimetype="audio/mpeg",
        conditional=True,
        download_name=preview_path.name,
    )


@bp.get("/api/studio/media/<filename>")
def api_studio_media(filename: str):
    safe = Path(filename).name
    for base in (OUTPUT_DIR, UPLOADS_DIR):
        path = base / safe
        if path.is_file():
            ext = path.suffix.lower()
            mime = {
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".m4a": "audio/mp4",
                ".ogg": "audio/ogg",
            }.get(ext, "application/octet-stream")
            return send_file(str(path), mimetype=mime, conditional=True)
    abort(404)


def _audio_peaks(path: Path, *, bins: int = 256) -> tuple[list[float], int]:
    """Peak envelope for waveform canvas (pydub mono samples)."""
    from pydub import AudioSegment

    if not path.is_file():
        return [], 0
    audio = AudioSegment.from_file(str(path))
    if audio.channels > 1:
        audio = audio.set_channels(1)
    samples = audio.get_array_of_samples()
    if not samples:
        return [0.0] * bins, len(audio)
    peak = float(max(abs(min(samples)), abs(max(samples))) or 1.0)
    chunk = max(1, len(samples) // bins)
    peaks: list[float] = []
    for i in range(bins):
        start = i * chunk
        end = min(len(samples), start + chunk)
        if start >= end:
            peaks.append(0.0)
            continue
        local_max = max(abs(s) for s in samples[start:end])
        peaks.append(round(local_max / peak, 4))
    return peaks, len(audio)


def _waveform_for_track(task_id: str, track: str, state: dict[str, Any]) -> dict[str, Any]:
    safe = Path(task_id).name
    track = (track or "original").lower()
    peaks: list[float] = []
    duration_ms = int(state.get("duration_ms") or 0)

    if track == "original":
        name = state.get("original_audio")
        if name:
            path = OUTPUT_DIR / Path(str(name)).name
            if not path.is_file():
                try:
                    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

                    with STATE_LOCK:
                        task = AUTO_TASKS.get(safe)
                        if task:
                            orig = (task.get("info") or {}).get("original_audio_path")
                            if orig:
                                path = Path(orig)
                except ImportError:
                    pass
            peaks, duration_ms = _audio_peaks(path)
    elif track in ("translated", "tts", "dub_voice"):
        seg_peaks: list[float] = []
        for seg in state.get("segments") or []:
            fname = seg.get("file") or seg.get("fitted_file")
            if not fname:
                continue
            p = _resolve_task_audio(fname, task_id=safe)
            if not p.is_file():
                continue
            sp, _ = _audio_peaks(p, bins=32)
            seg_peaks.extend(sp)
        if seg_peaks:
            step = max(1, len(seg_peaks) // 256)
            peaks = [seg_peaks[i] for i in range(0, len(seg_peaks), step)][:256]
            while len(peaks) < 256:
                peaks.append(0.0)
        duration_ms = duration_ms or 120000
    elif track in ("video", "user_voice", "music", "fx", "sfx", "markers"):
        peaks = [0.0] * 256
    else:
        peaks = [0.0] * 256

    return {"ok": True, "track": track, "peaks": peaks, "duration_ms": duration_ms}


@bp.get("/api/studio/waveform/<task_id>/<track>")
def api_studio_waveform(task_id: str, track: str):
    """Return peak JSON for timeline waveform rendering."""
    safe = Path(task_id).name
    blocked = _studio_access(safe)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    state = _load_session(safe)
    payload = _waveform_for_track(safe, track, state)
    return jsonify(payload)
