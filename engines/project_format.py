"""Unified .tdproj save/load — JSON zip format (TZ §13)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from engines.tubedub.project.model import TDPROJ_EXTENSION, TdProject
from engines.tubedub.project.store import TdProjectStore, get_project_store


def save_tdproj_zip(
    app_dir: Path,
    project: TdProject,
    *,
    word_timing: list[dict[str, Any]] | None = None,
    emotions: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
    video_ref: str | None = None,
) -> Path:
    """Save project as .tdproj zip with segments, word_timing, emotions, history."""
    store = get_project_store(app_dir)
    if video_ref:
        project.assets = project.assets or []
        if not any(a.path == video_ref for a in project.assets):
            from engines.tubedub.project.model import TdAssetRef

            project.assets.append(
                TdAssetRef(asset_id="video", path=video_ref, kind="video")
            )
    if word_timing:
        project.pipeline.segments = project.pipeline.segments or []
        project.module_states = project.module_states or []
        for ms in project.module_states:
            if ms.module_id == "word_timing":
                ms.data["word_maps"] = word_timing
                break
        else:
            from engines.tubedub.project.model import TdModuleState

            project.module_states.append(
                TdModuleState(module_id="word_timing", data={"word_maps": word_timing})
            )
    if emotions:
        for ms in project.module_states or []:
            if ms.module_id == "emotion":
                ms.data["tags"] = emotions
                break
        else:
            from engines.tubedub.project.model import TdModuleState

            project.module_states.append(
                TdModuleState(module_id="emotion", data={"tags": emotions})
            )
    if history:
        project.pipeline.stages = list(history)

    saved = store.save(project)
    zip_path = store._project_path(saved.project_id, saved.title).with_suffix(".tdproj.zip")
    manifest = saved.to_dict()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        if word_timing:
            zf.writestr("word_timing.json", json.dumps(word_timing, ensure_ascii=False))
        if emotions:
            zf.writestr("emotions.json", json.dumps(emotions, ensure_ascii=False))
        if history:
            zf.writestr("history.json", json.dumps(history, ensure_ascii=False))
        if video_ref and Path(video_ref).is_file():
            zf.write(video_ref, arcname=f"assets/{Path(video_ref).name}")
    return zip_path


def load_tdproj_zip(path: Path, app_dir: Path) -> TdProject | None:
    p = Path(path)
    if not p.is_file():
        return None
    if p.suffix == ".zip" or p.name.endswith(".tdproj.zip"):
        with zipfile.ZipFile(p, "r") as zf:
            raw = zf.read("project.json")
            data = json.loads(raw.decode("utf-8"))
            project = TdProject.from_dict(data)
            store = get_project_store(app_dir)
            return store.save(project)
    if p.suffix == TDPROJ_EXTENSION or p.name.endswith(TDPROJ_EXTENSION):
        return get_project_store(app_dir).load_by_path(p)
    return None


def autosave_from_task_info(app_dir: Path, task_info: dict[str, Any], *, title: str = "") -> TdProject | None:
    """Hook for pipeline autosave when task completes."""
    if not task_info:
        return None
    store = TdProjectStore(app_dir)
    project = store.create_empty(title=title or f"autosave-{task_info.get('task_id', 'task')}")
    segments = task_info.get("segments_data") or []
    project.pipeline.segments = [
        s for s in segments if isinstance(s, dict)
    ]
    word_maps = task_info.get("source_word_maps") or task_info.get("merged_word_maps")
    emotions = task_info.get("emotion_tags")
    history = task_info.get("pipeline_history")
    video = task_info.get("video_path_backup") or task_info.get("video_path")
    save_tdproj_zip(
        app_dir,
        project,
        word_timing=word_maps if isinstance(word_maps, list) else None,
        emotions=emotions if isinstance(emotions, list) else None,
        history=history if isinstance(history, list) else None,
        video_ref=str(video) if video else None,
    )
    return project


def autosave_studio_state(app_dir: Path, state: dict[str, Any]) -> TdProject | None:
    """Autosave studio session segments on edit."""
    if not state or not state.get("segments"):
        return None
    store = TdProjectStore(app_dir)
    sid = str(state.get("session_id") or "studio-default")
    title = f"studio-{sid}"
    project = store.create_empty(title=title)
    project.pipeline.segments = [
        s for s in (state.get("segments") or []) if isinstance(s, dict)
    ]
    project.module_states = project.module_states or []
    from engines.tubedub.project.model import TdModuleState

    project.module_states.append(
        TdModuleState(
            module_id="dub_studio",
            data={
                "timing_map": state.get("timing_map") or [],
                "tracks": state.get("tracks") or {},
                "plugin_order": state.get("plugin_order") or [],
                "duration_ms": state.get("duration_ms"),
            },
        )
    )
    emotions = [
        s.get("tts_emotion") or s.get("emotion")
        for s in project.pipeline.segments
        if s.get("tts_emotion") or s.get("emotion")
    ]
    save_tdproj_zip(
        app_dir,
        project,
        emotions=emotions if emotions else None,
    )
    return project
