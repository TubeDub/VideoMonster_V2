"""Developer log for feature flag lifecycle."""

from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any

_LOG: dict[str, "DeveloperLog"] = {}
_LOCK = threading.RLock()


class DeveloperLog:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._dir = self.app_dir / "output" / "dev" / "feature_flags"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._dir / "developer.log"
        self._json_path = self._dir / "events.json"
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def log(
        self,
        *,
        event: str,
        feature_id: str = "",
        message: str = "",
        duration_ms: float = 0.0,
        memory_mb: float | None = None,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "ts_ms": int(time.time() * 1000),
            "event": event,
            "feature_id": feature_id,
            "message": message,
            "duration_ms": round(duration_ms, 2),
            "memory_mb": memory_mb,
            "error": error,
            "meta": meta or {},
        }
        with self._lock:
            self._events.append(row)
            if len(self._events) > 2000:
                self._events = self._events[-1500:]
            line = (
                f"{row['ts_ms']}\t{event}\t{feature_id}\t{message}\t"
                f"dur={row['duration_ms']}ms"
                + (f"\terr={error}" if error else "")
                + "\n"
            )
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line)
            self._json_path.write_text(
                json.dumps({"events": self._events[-500:]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def log_exception(self, feature_id: str, exc: BaseException, *, context: str = "") -> None:
        self.log(
            event="feature_error",
            feature_id=feature_id,
            message=context or type(exc).__name__,
            error="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:],
        )

    def snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events[-limit:])


def get_dev_log(app_dir: Path | None = None) -> DeveloperLog:
    base = Path(app_dir or Path(__file__).resolve().parents[2])
    key = str(base.resolve())
    with _LOCK:
        if key not in _LOG:
            _LOG[key] = DeveloperLog(base)
        return _LOG[key]
