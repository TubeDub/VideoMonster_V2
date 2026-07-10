"""Platform bootstrap — single entry point for architecture initialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def bootstrap_platform(app_dir: Path) -> dict[str, Any]:
    from engines.tubedub.module_manager import get_module_manager

    mgr = get_module_manager(app_dir)
    mgr.bootstrap()
    return {
        "ok": True,
        "modules_loaded": len(mgr.all_modules()),
        "health": mgr.health_all(),
    }
