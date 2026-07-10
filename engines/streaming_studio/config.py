"""Streaming studio configuration."""

from __future__ import annotations

import os


def default_rtmp_url() -> str:
    return (os.getenv("VM_STREAMING_RTMP_URL") or "").strip()


def max_tracks() -> int:
    raw = (os.getenv("VM_STREAMING_MAX_TRACKS") or "4").strip()
    try:
        return max(1, min(16, int(raw)))
    except ValueError:
        return 4
