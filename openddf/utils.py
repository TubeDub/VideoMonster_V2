"""
Open Developer Diagnostic Framework (OpenDDF) v0.1.0 — Security utilities.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "****** [REDACTED BY OPENDDF] ******"

# Architectural / diagnostic keys that contain "key" but are not secrets.
_SAFE_KEY_NAMES = frozenset(
    {
        "slot_fit_key",
        "fit_key",
        "cache_key",
        "segment_key",
        "timing_key",
        "missing_preserved_tokens",
        "preserved_tokens",
    }
)

_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(api_key|secret_key|private_key|access_key|auth_key|password|passwd|token|secret|credential)s?$|"
    r"^(key|token|password|passwd|secret|auth|cookie|private|credential)$",
    re.IGNORECASE,
)

_USER_PATH_RE = re.compile(
    r"(?:/home/[^/\s]+|C:\\Users\\[^\\\s]+)",
    re.IGNORECASE,
)


def _redact_string(value: str) -> str:
    if _USER_PATH_RE.search(value):
        return _USER_PATH_RE.sub("<USER>", value)
    return value


def filter_sensitive_data(data: Any) -> Any:
    """Recursively mask sensitive keys and shorten user-specific paths."""
    if isinstance(data, dict):
        out: dict[Any, Any] = {}
        for key, value in data.items():
            key_str = str(key)
            if key_str in _SAFE_KEY_NAMES:
                out[key] = filter_sensitive_data(value)
            elif _SENSITIVE_KEY_RE.search(key_str):
                out[key] = REDACTED
            else:
                out[key] = filter_sensitive_data(value)
        return out
    if isinstance(data, (list, tuple)):
        filtered = [filter_sensitive_data(item) for item in data]
        return type(data)(filtered) if isinstance(data, tuple) else filtered
    if isinstance(data, str):
        return _redact_string(data)
    return data
