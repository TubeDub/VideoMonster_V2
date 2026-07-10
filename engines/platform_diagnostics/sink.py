"""Persist platform traces to output/dev/{module}/."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from engines.platform.config import platform_diagnostics_enabled
from engines.platform_diagnostics.trace import PlatformTraceRecord


class PlatformTraceSink:
    def __init__(self, app_dir: Path, *, module: str, session_id: str):
        self.app_dir = Path(app_dir)
        self.module = module
        self.session_id = session_id
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._dir = self.app_dir / "output" / "dev" / module
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{module}_{session_id}.json"

    @property
    def path(self) -> str:
        return str(self._path)

    def append(self, record: PlatformTraceRecord) -> None:
        if not platform_diagnostics_enabled():
            return
        row = record.to_dict()
        with self._lock:
            self._records.append(row)
            self._flush_locked()

    def log(
        self,
        *,
        stage: str,
        input_preview: str = "",
        output_preview: str = "",
        duration_ms: float = 0.0,
        engine: str = "",
        router_reason: str = "",
        quality_score: float | None = None,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        import time

        self.append(
            PlatformTraceRecord(
                stage=stage,
                session_id=self.session_id,
                module=self.module,
                ts_ms=int(time.time() * 1000),
                input_preview=input_preview[:500],
                output_preview=output_preview[:500],
                duration_ms=duration_ms,
                engine=engine,
                router_reason=router_reason,
                quality_score=quality_score,
                error=error,
                meta=dict(meta or {}),
            )
        )

    def _flush_locked(self) -> None:
        payload = {
            "module": self.module,
            "session_id": self.session_id,
            "records": list(self._records),
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "module": self.module,
                "session_id": self.session_id,
                "path": str(self._path),
                "record_count": len(self._records),
                "records": list(self._records),
            }
