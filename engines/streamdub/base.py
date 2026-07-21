"""StreamDub module interface — every stage implements this contract."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModuleState(str, Enum):
    CREATED = "created"
    INITIALIZED = "initialized"
    READY = "ready"
    SHUTDOWN = "shutdown"
    ERROR = "error"


@dataclass
class ModuleCapabilities:
    module_id: str
    version: str = "1.0"
    supports_async: bool = True
    backends: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "version": self.version,
            "supports_async": self.supports_async,
            "backends": list(self.backends),
            "features": list(self.features),
            "meta": dict(self.meta),
        }


@dataclass
class HealthStatus:
    ok: bool
    module_id: str
    state: str
    message: str = ""
    latency_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "module_id": self.module_id,
            "state": self.state,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "meta": dict(self.meta),
        }


class StreamModule(ABC):
    """Unified lifecycle for every StreamDub stage."""

    module_id: str = "base"

    def __init__(self) -> None:
        self._state = ModuleState.CREATED
        self._initialized = False

    @property
    def state(self) -> ModuleState:
        return self._state

    def initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        if self._initialized:
            return
        self._on_initialize(app_dir=app_dir, config=config or {})
        self._initialized = True
        self._state = ModuleState.INITIALIZED

    @abstractmethod
    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        ...

    @abstractmethod
    def process(self, payload: Any) -> Any:
        ...

    def shutdown(self) -> None:
        self._on_shutdown()
        self._state = ModuleState.SHUTDOWN

    def _on_shutdown(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            ok, msg, meta = self._on_health_check()
            return HealthStatus(
                ok=ok,
                module_id=self.module_id,
                state=self._state.value,
                message=msg,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                meta=meta or {},
            )
        except Exception as exc:
            return HealthStatus(
                ok=False,
                module_id=self.module_id,
                state=ModuleState.ERROR.value,
                message=str(exc),
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

    @abstractmethod
    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        ...

    @abstractmethod
    def capabilities(self) -> ModuleCapabilities:
        ...
