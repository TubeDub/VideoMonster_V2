"""Unified trace record for all platform modules."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PlatformTraceRecord:
    stage: str
    session_id: str
    module: str
    ts_ms: int = 0
    input_preview: str = ""
    output_preview: str = ""
    duration_ms: float = 0.0
    engine: str = ""
    router_reason: str = ""
    quality_score: float | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class trace_stage:
    """Context manager: log stage duration + in/out previews."""

    def __init__(
        self,
        sink: Any,
        *,
        stage: str,
        module: str,
        session_id: str,
        input_preview: str = "",
        engine: str = "",
        router_reason: str = "",
    ):
        self.sink = sink
        self.stage = stage
        self.module = module
        self.session_id = session_id
        self.input_preview = input_preview[:500]
        self.engine = engine
        self.router_reason = router_reason
        self._t0 = 0.0
        self.output_preview = ""
        self.quality_score: float | None = None
        self.error: str | None = None
        self.meta: dict[str, Any] = {}

    def __enter__(self) -> "trace_stage":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.error = str(exc)
        dur = (time.perf_counter() - self._t0) * 1000.0
        rec = PlatformTraceRecord(
            stage=self.stage,
            session_id=self.session_id,
            module=self.module,
            ts_ms=int(time.time() * 1000),
            input_preview=self.input_preview,
            output_preview=self.output_preview[:500],
            duration_ms=round(dur, 2),
            engine=self.engine,
            router_reason=self.router_reason,
            quality_score=self.quality_score,
            error=self.error,
            meta=dict(self.meta),
        )
        if self.sink is not None:
            self.sink.append(rec)
        return False
