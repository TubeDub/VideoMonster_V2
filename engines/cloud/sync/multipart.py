"""Multipart transfer helpers with resume support."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable


def iter_file_chunks(path: Path, chunk_size: int):
    with open(path, "rb") as f:
        idx = 0
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            yield idx, data
            idx += 1


def multipart_copy_upload(
    path: Path,
    chunk_size: int,
    upload_part: Callable[[bytes, int, int], None],
) -> int:
    parts = list(iter_file_chunks(path, chunk_size))
    total = len(parts)
    if total == 0:
        upload_part(b"", 0, 1)
        return 0
    for idx, data in parts:
        upload_part(data, idx, total)
    return total


class TransferState:
    """Persisted resume state for interrupted transfers."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, transfer_id: str) -> Path:
        safe = transfer_id.replace("/", "_").replace("\\", "_")
        return self.state_dir / f"{safe}.json"

    def save(self, transfer_id: str, data: dict) -> None:
        data = {**data, "updated_ms": int(time.time() * 1000)}
        self._path(transfer_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, transfer_id: str) -> dict | None:
        p = self._path(transfer_id)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def clear(self, transfer_id: str) -> None:
        p = self._path(transfer_id)
        if p.is_file():
            p.unlink()
