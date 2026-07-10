"""Checksum utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    if not expected:
        return True
    return file_sha256(path) == expected.strip().lower()
