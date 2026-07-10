"""Enterprise developer log — per-segment structured trace."""

from __future__ import annotations

import json
import time
from pathlib import Path

from engines.enterprise_translation.config import DEV_LOG_NAME


class EnterpriseDevLog:
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        self._path = app_dir / "logs" / DEV_LOG_NAME
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_segment(self, payload: dict) -> None:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **payload,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
