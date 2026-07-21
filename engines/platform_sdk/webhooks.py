"""P719 Webhooks — Pipeline / Export / Diagnostics / Release events."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib import request

from engines.platform_sdk.event_bus import get_platform_bus
from engines.platform_sdk.types import PlatformEvent

ROOT = Path(__file__).resolve().parents[2]

WEBHOOK_EVENTS = (
    PlatformEvent.PIPELINE_FINISHED,
    PlatformEvent.EXPORT_FINISHED,
    PlatformEvent.PROJECT_FAILED,
    PlatformEvent.DIAGNOSTICS_READY,
    PlatformEvent.RELEASE_READY,
)


class WebhookRegistry:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or (ROOT / "data" / "webhooks.json"))
        self._hooks: list[dict[str, Any]] = []
        self._load()
        self._wired = False

    def _load(self) -> None:
        if self.path.is_file():
            try:
                self._hooks = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._hooks = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._hooks, indent=2), encoding="utf-8")

    def register(
        self,
        url: str,
        *,
        events: list[str] | None = None,
        secret: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        row = {
            "name": name or url,
            "url": url,
            "events": list(events or [e.value for e in WEBHOOK_EVENTS]),
            "secret": secret,
            "created_at": time.time(),
        }
        self._hooks.append(row)
        self._save()
        self.ensure_wired()
        return {k: v for k, v in row.items() if k != "secret"}

    def list_hooks(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in h.items() if k != "secret"} for h in self._hooks]

    def ensure_wired(self) -> None:
        if self._wired:
            return
        bus = get_platform_bus()

        def _forward(event: str, record: dict[str, Any]) -> None:
            self.dispatch(event, record.get("payload") or {})

        for ev in WEBHOOK_EVENTS:
            bus.subscribe(ev, _forward)
        self._wired = True

    def dispatch(self, event: str, payload: dict[str, Any], *, dry_run: bool = False) -> list[dict[str, Any]]:
        results = []
        body = json.dumps({"event": event, "payload": payload, "ts": time.time()}, ensure_ascii=False).encode("utf-8")
        for hook in self._hooks:
            if event not in (hook.get("events") or []):
                continue
            sig = ""
            if hook.get("secret"):
                sig = hmac.new(hook["secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
            row = {"url": hook["url"], "event": event, "ok": True}
            if dry_run:
                row["dry_run"] = True
                row["signature"] = sig
                results.append(row)
                continue
            try:
                req = request.Request(
                    hook["url"],
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-VM-Signature": sig,
                        "X-VM-Event": event,
                    },
                    method="POST",
                )
                with request.urlopen(req, timeout=5) as resp:
                    row["status"] = getattr(resp, "status", 200)
            except Exception as exc:
                row["ok"] = False
                row["error"] = str(exc)
            results.append(row)
        return results


_HOOKS: WebhookRegistry | None = None


def get_webhook_registry(**kwargs: Any) -> WebhookRegistry:
    global _HOOKS
    if _HOOKS is None or kwargs:
        _HOOKS = WebhookRegistry(**kwargs)
    return _HOOKS
