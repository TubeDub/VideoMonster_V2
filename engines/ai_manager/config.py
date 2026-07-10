"""AI Module configuration persistence (TubeDub AI Manager v1.0)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()

STATUS_NOT_INSTALLED = "not_installed"
STATUS_INSTALLING = "installing"
STATUS_READY = "ready"
STATUS_ERROR = "error"

# User-facing backend label — never expose internal provider names in UI.
BACKEND_LABEL = "Локальный AI-модуль TubeDub"

# Default model family: DeepSeek (product default). Fallback chain in
# engines.llm_providers.registry picks the next installed family when missing.
# Override with VM_TRANSLATE_MODEL or Settings → AI Model.
DEFAULT_MODEL = "deepseek-r1:7b"
DEFAULT_PROVIDER = "deepseek"
# Dub adaptation speed: max_quality (default) | balanced | fast
DEFAULT_QUALITY_MODE = "max_quality"
INSTALLER_MIN_BYTES = 100_000_000  # reject truncated downloads (~100 MB floor)
INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
# Approximate total download for dialog (installer ≈1.5 GB + model ≈2 GB).
ESTIMATED_DOWNLOAD_GB = 3.5


def _config_path(app_dir: Path) -> Path:
    return Path(app_dir) / "data" / "ai_module.json"


def default_config() -> dict[str, Any]:
    return {
        "status": STATUS_NOT_INSTALLED,
        "deferred": False,
        "deferred_at": None,
        "installed_by_tubedub": False,
        "backend_label": BACKEND_LABEL,
        "backend_internal": None,
        "selected_provider": DEFAULT_PROVIDER,
        "quality_mode": DEFAULT_QUALITY_MODE,
        "model": DEFAULT_MODEL,
        "installed_at": None,
        "size_bytes": 0,
        "install_progress": {"phase": "", "percent": 0, "message": ""},
        "install_log": [],
        "last_verification": None,
        "last_error": None,
    }


def load_config(app_dir: Path) -> dict[str, Any]:
    path = _config_path(app_dir)
    with _LOCK:
        if not path.is_file():
            return default_config()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            base = default_config()
            base.update({k: v for k, v in data.items() if k in base or k in data})
            return base
        except Exception:
            return default_config()


def save_config(app_dir: Path, cfg: dict[str, Any]) -> None:
    path = _config_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(app_dir: Path, message: str, *, level: str = "info") -> None:
    try:
        from engines.install_log import install_log

        install_log(message, level=level, component="ai_module")
    except Exception:
        pass
    cfg = load_config(app_dir)
    log = cfg.setdefault("install_log", [])
    log.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
        }
    )
    cfg["install_log"] = log[-200:]
    save_config(app_dir, cfg)


def set_progress(app_dir: Path, phase: str, percent: float, message: str) -> None:
    cfg = load_config(app_dir)
    cfg["install_progress"] = {
        "phase": phase,
        "percent": round(max(0, min(100, percent)), 1),
        "message": message,
    }
    if cfg.get("status") != STATUS_INSTALLING:
        cfg["status"] = STATUS_INSTALLING
    save_config(app_dir, cfg)
