"""Централизованные логи в output/logs/ — не показываются пользователю в UI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Windows: не падать, если tubedub.log занят другим процессом TubeDub."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            pass
        except OSError:
            pass


def setup_app_logging(app_dir: Path, level: int = logging.INFO) -> Path:
    log_dir = app_dir / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "tubedub.log"

    root = logging.getLogger()
    if getattr(setup_app_logging, "_configured", False):
        return log_file
    setup_app_logging._configured = True  # type: ignore[attr-defined]

    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = _SafeRotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return log_file
