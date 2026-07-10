"""AutoDub Project Package — terminal artifact for Dub Studio handoff."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.autodub.project_package")


def build_autodub_project_package(
    app_dir: Path,
    task_id: str,
    task_info: dict[str, Any],
    *,
    title: str = "",
) -> Path | None:
    """
    Save AutoDub output as .tdproj.zip — opens in Dub Studio without re-running AI Core.

    Contains segments, timings, TTS files references, verification reports, and video ref.
    """
    if not task_info:
        return None

    from engines.project_format import autosave_from_task_info, save_tdproj_zip

    project = autosave_from_task_info(
        app_dir,
        task_info,
        title=title or f"autodub-{task_id}",
    )
    if project is None:
        return None

    store_root = app_dir / "projects" / "tdproj"
    candidates = sorted(store_root.glob("*.tdproj.zip"), key=lambda p: p.stat().st_mtime)
    zip_path = candidates[-1] if candidates else None

    package_meta = {
        "task_id": task_id,
        "format": "tubedub-project-package",
        "version": 1,
        "source": "autodub",
        "ai_core_complete": True,
        "voice_verification_passed": bool(task_info.get("voice_verification_passed")),
        "reviewer_agent_path": bool(task_info.get("reviewer_agent_path")),
        "paths": {
            "voice_verification_report": task_info.get("voice_verification_report_path"),
            "reviewer_loop": task_info.get("reviewer_loop_path"),
            "translation_validation": task_info.get("translation_validation_path"),
        },
    }
    meta_dir = app_dir / "output" / "diagnostics" / task_id
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / "project_package.json"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(package_meta, fh, ensure_ascii=False, indent=2)

    task_info["project_package_path"] = str(zip_path) if zip_path else ""
    task_info["project_package_meta_path"] = str(meta_path)
    task_info["autodub_complete"] = True
    task_info["dub_studio_entry"] = "project_package"

    logger.info(
        "[AutoDub] project package task=%s path=%s verification=%s",
        task_id,
        zip_path,
        task_info.get("voice_verification_passed"),
    )
    return zip_path
