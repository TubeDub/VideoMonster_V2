"""Atomic filesystem primitives (Storage Manager §5).

Guarantees:
  * никакой частичной записи — файл появляется целиком или не появляется вовсе;
  * защита от повреждения при отключении питания — данные пишутся во временный
    файл, сбрасываются на диск (fsync), затем атомарно переименовываются;
  * запись директории тоже подтверждается fsync (POSIX), где это возможно.

Схема: temp file -> full write -> flush + fsync -> os.replace().
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Suffixes considered "incomplete writes" — safe to purge during cleanup.
TEMP_SUFFIXES = (".tmp", ".partial", ".writing")


def _fsync_dir(directory: Path) -> None:
    """Best-effort fsync of a directory entry (no-op on platforms without it)."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except (OSError, ValueError):
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    """Atomically write raw bytes to ``path``.

    Writes a sibling temp file, fsyncs it, then ``os.replace`` (atomic rename)
    onto the target. Never leaves the destination partially written.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".writing"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        _fsync_dir(target.parent)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Atomically write text to ``path``."""
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(
    path: str | Path,
    obj: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
) -> Path:
    """Atomically serialize ``obj`` as JSON to ``path``."""
    payload = json.dumps(
        obj, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys
    )
    return atomic_write_text(path, payload)


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` on any error (missing/corrupt file)."""
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
