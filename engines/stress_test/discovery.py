"""Discover stress test videos and per-video settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engines.stress_test.config import VIDEO_EXTENSIONS, app_dir, stress_tests_dir


def _default_manifest() -> dict[str, Any]:
    return {
        "version": 1,
        "defaults": {
            "source_lang": "en",
            "target_lang": "uk",
            "voice": "uk-UA-OstapNeural",
            "model_size": "tiny",
            "dub_style": "modern",
            "translation_review_before_tts": False,
            "mix_volume": 0.3,
        },
        "videos": {},
    }


def load_manifest(base: Path | None = None) -> dict[str, Any]:
    path = stress_tests_dir(base) / "manifest.json"
    if not path.is_file():
        return _default_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_manifest()
        data.setdefault("defaults", _default_manifest()["defaults"])
        data.setdefault("videos", {})
        return data
    except Exception:
        return _default_manifest()


def list_test_videos(base: Path | None = None) -> list[dict[str, Any]]:
    root = stress_tests_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(base)
    defaults = dict(manifest.get("defaults") or {})
    overrides = manifest.get("videos") or {}

    items: list[dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        name = path.name
        cfg = {**defaults, **(overrides.get(name) or overrides.get(path.stem) or {})}
        items.append(
            {
                "name": name,
                "path": str(path.resolve()),
                "relative": f"data/stress_tests/{name}",
                **cfg,
            }
        )
    return items


def ensure_sample_hint(base: Path | None = None) -> None:
    """Create manifest + README if folder is empty."""
    root = stress_tests_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        manifest_path.write_text(
            json.dumps(_default_manifest(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    readme = root / "README.txt"
    if not readme.is_file():
        readme.write_text(
            "Place short MP4/MKV test videos here for Stress Test Center.\n"
            "Optional: edit manifest.json for per-video target_lang / voice.\n",
            encoding="utf-8",
        )

    # Bootstrap one sample from E2E test video if folder has no videos yet
    has_video = any(
        p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS for p in root.iterdir()
    )
    if not has_video:
        base = app_dir(base)
        for candidate in (
            base / "uploads" / "test_e2e_speech.mp4",
            base / "data" / "stress_tests" / "sample_placeholder.mp4",
        ):
            if candidate.is_file():
                import shutil

                dest = root / candidate.name
                if not dest.exists():
                    shutil.copy2(candidate, dest)
                break
