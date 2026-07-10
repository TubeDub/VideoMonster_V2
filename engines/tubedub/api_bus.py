"""Public API bus — mandatory inter-module communication layer."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ApiRequest:
    request_id: str
    namespace: str
    method: str
    payload: dict[str, Any]
    caller: str = "system"
    created_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "namespace": self.namespace,
            "method": self.method,
            "payload": dict(self.payload),
            "caller": self.caller,
            "created_ms": self.created_ms,
        }


@dataclass
class ApiResponse:
    request_id: str
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "ok": self.ok,
            "result": dict(self.result),
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class RouteRecord:
    namespace: str
    method: str
    module_id: str
    description: str = ""

    def key(self) -> str:
        return f"{self.namespace}.{self.method}"


class ApiBus:
    """All module interactions go through this bus — no direct cross-module calls."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[str, Handler] = {}
        self._routes: dict[str, RouteRecord] = {}
        self._log: list[dict[str, Any]] = []
        self._max_log = 500

    def register(
        self,
        namespace: str,
        method: str,
        handler: Handler,
        *,
        module_id: str,
        description: str = "",
    ) -> None:
        key = f"{namespace}.{method}"
        with self._lock:
            self._handlers[key] = handler
            self._routes[key] = RouteRecord(namespace, method, module_id, description)

    def unregister(self, namespace: str, method: str) -> None:
        key = f"{namespace}.{method}"
        with self._lock:
            self._handlers.pop(key, None)
            self._routes.pop(key, None)

    def call(
        self,
        namespace: str,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        caller: str = "system",
    ) -> ApiResponse:
        key = f"{namespace}.{method}"
        req = ApiRequest(
            request_id=uuid.uuid4().hex[:12],
            namespace=namespace,
            method=method,
            payload=dict(payload or {}),
            caller=caller,
            created_ms=int(time.time() * 1000),
        )
        t0 = time.perf_counter()
        with self._lock:
            handler = self._handlers.get(key)
        if not handler:
            resp = ApiResponse(
                request_id=req.request_id,
                ok=False,
                error=f"Unknown API route: {key}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
            self._append_log(req, resp)
            return resp
        try:
            result = handler(dict(req.payload))
            if not isinstance(result, dict):
                result = {"value": result}
            resp = ApiResponse(
                request_id=req.request_id,
                ok=True,
                result=result,
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except Exception as exc:
            resp = ApiResponse(
                request_id=req.request_id,
                ok=False,
                error=str(exc),
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        self._append_log(req, resp)
        return resp

    def list_routes(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "namespace": r.namespace,
                    "method": r.method,
                    "module_id": r.module_id,
                    "description": r.description,
                    "key": r.key(),
                }
                for r in sorted(self._routes.values(), key=lambda x: x.key())
            ]

    def recent_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log[-limit:])

    def _append_log(self, req: ApiRequest, resp: ApiResponse) -> None:
        with self._lock:
            self._log.append(
                {
                    "request": req.to_dict(),
                    "response": resp.to_dict(),
                    "route": f"{req.namespace}.{req.method}",
                }
            )
            if len(self._log) > self._max_log:
                self._log = self._log[-self._max_log :]


_BUS: ApiBus | None = None
_BUS_LOCK = threading.Lock()


def get_api_bus() -> ApiBus:
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = ApiBus()
        return _BUS
