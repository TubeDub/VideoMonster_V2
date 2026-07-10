"""Unified install/prepare log — logs/install.log."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.RLock()
_LOGGER = logging.getLogger("tubedub.install")

APP_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = APP_DIR / "logs"
LOG_PATH = LOG_DIR / "install.log"


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def install_log(message: str, *, level: str = "info", component: str = "") -> None:
    """Append one line to logs/install.log and the tubedub.install logger."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    comp = f"[{component}] " if component else ""
    line = f"[{ts}] {level.upper():5} {comp}{message}"
    with _LOCK:
        try:
            _ensure_log_dir()
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    try:
        getattr(_LOGGER, level, _LOGGER.info)(line)
    except Exception:
        pass


def read_tail(limit: int = 80) -> list[str]:
    if not LOG_PATH.is_file():
        return []
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max(1, limit) :]
    except Exception:
        return []
