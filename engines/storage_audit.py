"""TubeDub storage audit — show what occupies disk space (TZ Storage §8).

Buckets are read-only measurements. Protected buckets (projects, models, settings,
final MP4, presets) are never included in automated cleanup targets.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Directories that must NEVER be touched by automated / manual temp cleanup.
PROTECTED_TOP_LEVEL = frozenset(
    {
        "projects",
        "data",
        "models",
        "uploads",
        "templates",
        "static",
        "engines",
        "api",
        "tests",
    }
)


def _mb(n: int) -> float:
    return round(n / 1024**2, 2)


def _gb(n: int) -> float:
    return round(n / 1024**3, 3)


def _safe_dir_size(path: Path) -> int:
    try:
        from engines.model_manager.storage import dir_size

        return int(dir_size(path))
    except Exception:
        total = 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        if not path.is_dir():
            return 0
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total


def _program_size(app_dir: Path) -> int:
    """Application code + static assets (excludes models, output, cache trees)."""
    total = 0
    include_roots = ("engines", "api", "static", "templates", "data", "openddf", "tools")
    exclude_under_data = {"cache", "model_manager.json"}  # settings stay small; cache counted separately
    for name in include_roots:
        root = app_dir / name
        if not root.is_dir():
            continue
        if name == "data":
            for p in root.iterdir():
                if p.name in exclude_under_data or p.name.endswith(".local.json"):
                    continue
                total += _safe_dir_size(p)
            continue
        total += _safe_dir_size(root)
    # Root-level app files
    for p in app_dir.iterdir():
        if p.is_file() and p.suffix.lower() in (".py", ".txt", ".md", ".json", ".html"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _ollama_external_size() -> tuple[int, str | None]:
    """Best-effort size of external Ollama model store (not managed by TubeDub)."""
    home = Path.home()
    candidates = [
        home / ".ollama" / "models",
        Path(os.getenv("OLLAMA_MODELS", "")) if os.getenv("OLLAMA_MODELS") else None,
    ]
    for cand in candidates:
        if cand and cand.is_dir():
            return _safe_dir_size(cand), str(cand)
    return 0, None


def audit_storage(app_dir: Path) -> dict[str, Any]:
    """Full storage breakdown for Settings «Хранилище» and diagnostics."""
    app_dir = Path(app_dir)
    output = app_dir / "output"
    models_root = app_dir / "models"
    hf_cache = app_dir / "cache" / "huggingface"
    pipeline_cache = output / "cache" / "pipeline"
    sessions = output / "sessions"
    slot_fit = output / "slot_fit"
    logs_dir = output / "logs"
    dev_dir = output / "dev"
    diagnostics = output / "diagnostics"
    projects = app_dir / "projects"
    llm_cache_file = app_dir / "data" / "cache" / "llm_rewrite_cache.json"
    uploads = app_dir / "uploads"

    # AI models (HF hub + components)
    try:
        from engines.model_manager import get_storage_status

        model_status = get_storage_status(app_dir)
        models_bytes = int(model_status.get("total_bytes") or 0)
    except Exception:
        model_status = {}
        models_bytes = _safe_dir_size(models_root) + _safe_dir_size(hf_cache)

    ollama_bytes, ollama_path = _ollama_external_size()
    try:
        from engines.ai_manager.installer import ai_module_disk_bytes

        ai_module_bytes = ai_module_disk_bytes(app_dir)
    except Exception:
        ai_module_bytes = ollama_bytes

    llm_rewrite_cache = _safe_dir_size(llm_cache_file) if llm_cache_file.is_file() else 0

    cache_bytes = (
        _safe_dir_size(pipeline_cache)
        + llm_rewrite_cache
        + _safe_dir_size(hf_cache / "tmp")
    )

    temp_bytes = (
        _safe_dir_size(sessions)
        + _safe_dir_size(slot_fit)
        + _temp_globs_size(output)
    )

    logs_bytes = _safe_dir_size(logs_dir) + _safe_dir_size(dev_dir) + _safe_dir_size(diagnostics)
    projects_bytes = _safe_dir_size(projects)
    program_bytes = _program_size(app_dir)

    # Final MP4 outputs (protected — display only)
    mp4_bytes = sum(
        p.stat().st_size for p in output.glob("*.mp4") if p.is_file()
    ) if output.is_dir() else 0

    total = (
        program_bytes
        + models_bytes
        + ollama_bytes
        + cache_bytes
        + temp_bytes
        + logs_bytes
        + projects_bytes
        + mp4_bytes
        + _safe_dir_size(uploads)
    )

    buckets = [
        {
            "id": "program",
            "label": "Размер программы",
            "bytes": program_bytes,
            "mb": _mb(program_bytes),
            "deletable": False,
            "protected_reason": "код приложения",
        },
        {
            "id": "models",
            "label": "Размер AI-моделей (перевод/STT)",
            "bytes": models_bytes,
            "mb": _mb(models_bytes),
            "path": str(models_root),
            "deletable": False,
            "protected_reason": "модели управляются через Менеджер моделей",
        },
        {
            "id": "ai_module",
            "label": "Размер AI-модуля",
            "bytes": ai_module_bytes,
            "mb": _mb(ai_module_bytes),
            "deletable": False,
            "protected_reason": "AI-модуль TubeDub (удаление через Настройки → AI Module)",
        },
        {
            "id": "llm",
            "label": "Размер кэша AI-адаптации",
            "bytes": llm_rewrite_cache,
            "mb": _mb(llm_rewrite_cache),
            "deletable": True,
            "deletable_scope": "llm_rewrite_cache",
        },
        {
            "id": "cache",
            "label": "Размер кэша",
            "bytes": cache_bytes,
            "mb": _mb(cache_bytes),
            "pipeline_cache_mb": _mb(_safe_dir_size(pipeline_cache)),
            "deletable": True,
            "deletable_scope": "pipeline_cache_only",
        },
        {
            "id": "temp",
            "label": "Размер временных файлов",
            "bytes": temp_bytes,
            "mb": _mb(temp_bytes),
            "sessions_mb": _mb(_safe_dir_size(sessions)),
            "slot_fit_mb": _mb(_safe_dir_size(slot_fit)),
            "deletable": True,
            "deletable_scope": "temp_only",
        },
        {
            "id": "logs",
            "label": "Размер логов",
            "bytes": logs_bytes,
            "mb": _mb(logs_bytes),
            "path": str(logs_dir),
            "deletable": False,
            "protected_reason": "диагностика и история",
        },
        {
            "id": "projects",
            "label": "Размер проектов",
            "bytes": projects_bytes,
            "mb": _mb(projects_bytes),
            "path": str(projects),
            "deletable": False,
            "protected_reason": "пользовательские проекты",
        },
        {
            "id": "outputs",
            "label": "Готовые MP4",
            "bytes": mp4_bytes,
            "mb": _mb(mp4_bytes),
            "deletable": False,
            "protected_reason": "готовые видео",
        },
    ]

    try:
        from engines.model_manager.storage import disk_usage_for_storage

        disk = disk_usage_for_storage(app_dir)
    except Exception:
        disk = {"free_gb": -1, "total_gb": -1}

    return {
        "buckets": buckets,
        "total_bytes": total,
        "total_mb": _mb(total),
        "total_gb": _gb(total),
        "disk_free_gb": disk.get("free_gb", -1),
        "disk_total_gb": disk.get("total_gb", -1),
        "storage_root": model_status.get("storage_root"),
        "deletable_temp_mb": _mb(temp_bytes + _safe_dir_size(pipeline_cache) + llm_rewrite_cache),
    }


def _temp_globs_size(output_dir: Path) -> int:
    """Size of loose temp artifact files in output/ root."""
    from engines.pipeline_cleanup import TEMP_GLOBS

    total = 0
    if not output_dir.is_dir():
        return 0
    seen: set[str] = set()
    for pattern in TEMP_GLOBS:
        for p in output_dir.glob(pattern):
            if not p.is_file():
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total
