"""Unified module lifecycle — mandatory for every TubeDub module."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModuleLifecycleState(str, Enum):
    CREATED = "created"
    INITIALIZED = "initialized"
    LOADED = "loaded"
    RUNNING = "running"
    STOPPED = "stopped"
    DISPOSED = "disposed"
    ERROR = "error"


@dataclass
class HealthReport:
    module_id: str
    state: str
    ok: bool
    message: str = ""
    latency_ms: float = 0.0
    load_pct: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    models_in_use: list[str] = field(default_factory=list)
    plugins_in_use: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "state": self.state,
            "ok": self.ok,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "load_pct": self.load_pct,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "models_in_use": list(self.models_in_use),
            "plugins_in_use": list(self.plugins_in_use),
            "meta": dict(self.meta),
        }


@dataclass
class ModuleContext:
    app_dir: str
    module_id: str
    api_namespace: str
    config: dict[str, Any] = field(default_factory=dict)


class PlatformModule(ABC):
    """
    Mandatory lifecycle for every TubeDub module.
    External code must interact only via ApiBus — never call sibling modules directly.
    """

    module_id: str = "base"
    api_namespace: str = "base"
    dependencies: list[str] = []

    def __init__(self) -> None:
        self._state = ModuleLifecycleState.CREATED
        self._ctx: ModuleContext | None = None
        self._last_run_ms: float = 0.0
        self._run_count: int = 0
        self._last_error: str = ""

    @property
    def state(self) -> ModuleLifecycleState:
        return self._state

    def initialize(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        self._on_initialize(ctx)
        self._state = ModuleLifecycleState.INITIALIZED

    def load(self) -> None:
        if self._state not in (ModuleLifecycleState.INITIALIZED, ModuleLifecycleState.STOPPED):
            raise RuntimeError(f"{self.module_id}: load() requires initialized state")
        self._on_load()
        self._state = ModuleLifecycleState.LOADED

    def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._state not in (ModuleLifecycleState.LOADED, ModuleLifecycleState.RUNNING):
            raise RuntimeError(f"{self.module_id}: run() requires loaded state")
        t0 = time.perf_counter()
        self._state = ModuleLifecycleState.RUNNING
        try:
            result = self._on_run(dict(payload or {}))
            self._last_run_ms = round((time.perf_counter() - t0) * 1000, 2)
            self._run_count += 1
            return result
        except Exception as exc:
            self._last_error = str(exc)
            self._state = ModuleLifecycleState.ERROR
            raise
        finally:
            if self._state == ModuleLifecycleState.RUNNING:
                self._state = ModuleLifecycleState.LOADED

    def stop(self) -> None:
        self._on_stop()
        self._state = ModuleLifecycleState.STOPPED

    def dispose(self) -> None:
        try:
            if self._state == ModuleLifecycleState.RUNNING:
                self.stop()
        except Exception:
            pass
        self._on_dispose()
        self._state = ModuleLifecycleState.DISPOSED

    def health_check(self) -> HealthReport:
        t0 = time.perf_counter()
        try:
            report = self._on_health_check()
            report.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            report.state = self._state.value
            return report
        except Exception as exc:
            return HealthReport(
                module_id=self.module_id,
                state=self._state.value,
                ok=False,
                message=str(exc),
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                errors=[str(exc)],
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "api_namespace": self.api_namespace,
            "state": self._state.value,
            "dependencies": list(self.dependencies),
            "last_run_ms": self._last_run_ms,
            "run_count": self._run_count,
            "last_error": self._last_error,
        }

    @abstractmethod
    def _on_initialize(self, ctx: ModuleContext) -> None: ...

    @abstractmethod
    def _on_load(self) -> None: ...

    @abstractmethod
    def _on_run(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def _on_stop(self) -> None: ...

    @abstractmethod
    def _on_dispose(self) -> None: ...

    @abstractmethod
    def _on_health_check(self) -> HealthReport: ...
