"""Session-scoped logging adapter."""

from __future__ import annotations

import logging
from typing import Any


class SessionLoggerAdapter(logging.LoggerAdapter):
    """Format: [Session <uuid>] [Module] message"""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        sid = self.extra.get("session_id", "?")
        module = self.extra.get("module", "core")
        short = str(sid)[:8] if sid else "?"
        return f"[Session {short}] [{module}] {msg}", kwargs
