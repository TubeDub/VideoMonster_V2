"""Storage locks (Storage Manager §6).

Two guarantees:
  * один проект нельзя открыть одновременно двумя процессами;
  * один проект нельзя одновременно писать двумя потоками.

Реализация:
  * ``_project_thread_lock`` — потоковые ``RLock`` на каждый project_id;
  * :class:`ProjectFileLock` — межпроцессная блокировка через lock-файл
    (msvcrt на Windows, fcntl на POSIX) с записью pid/host и защитой
    от «протухших» блокировок мёртвых процессов.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCK = threading.Lock()


def _project_thread_lock(project_id: str) -> threading.RLock:
    with _REGISTRY_LOCK:
        lock = _THREAD_LOCKS.get(project_id)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[project_id] = lock
        return lock


class StorageLockError(RuntimeError):
    """Raised when a project lock cannot be acquired."""

    def __init__(self, project_id: str, holder: dict | None = None):
        self.project_id = project_id
        self.holder = holder or {}
        who = self.holder.get("pid", "?")
        super().__init__(f"Project '{project_id}' is locked by pid={who}")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, AttributeError):
        return False


class ProjectFileLock:
    """Cross-process advisory lock backed by a lock file.

    Usage::

        with ProjectFileLock(path) as lock:
            ...  # exclusive access

    Raises :class:`StorageLockError` if held by another *live* process.
    Stale locks (owner process gone) are reclaimed automatically.
    """

    def __init__(self, lock_path: str | Path, *, project_id: str = "", timeout: float = 0.0):
        self.lock_path = Path(lock_path)
        self.project_id = project_id or self.lock_path.stem
        self.timeout = float(timeout)
        self._fh = None
        self._acquired = False

    def _holder_info(self) -> dict:
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _reclaim_if_stale(self) -> None:
        info = self._holder_info()
        pid = int(info.get("pid") or 0)
        if pid and pid != os.getpid() and not _pid_alive(pid):
            # Owner is dead — remove the stale lock.
            self.lock_path.unlink(missing_ok=True)

    def acquire(self) -> "ProjectFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            self._reclaim_if_stale()
            try:
                fh = open(self.lock_path, "a+")
                self._lock_handle(fh)
                fh.seek(0)
                fh.truncate()
                fh.write(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "host": socket.gethostname(),
                            "project_id": self.project_id,
                            "acquired_at": time.time(),
                        }
                    )
                )
                fh.flush()
                self._fh = fh
                self._acquired = True
                return self
            except (OSError, BlockingIOError):
                try:
                    fh.close()  # type: ignore[has-type]
                except Exception:
                    pass
                if time.time() >= deadline:
                    raise StorageLockError(self.project_id, self._holder_info())
                time.sleep(0.1)

    def _lock_handle(self, fh) -> None:
        if os.name == "nt":
            import msvcrt

            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except (OSError, PermissionError) as exc:
                raise BlockingIOError("locked") from exc
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_handle(self, fh) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def release(self) -> None:
        if not self._acquired or self._fh is None:
            return
        self._unlock_handle(self._fh)
        try:
            self._fh.close()
        except Exception:
            pass
        self.lock_path.unlink(missing_ok=True)
        self._fh = None
        self._acquired = False

    def __enter__(self) -> "ProjectFileLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def is_locked(lock_path: str | Path) -> bool:
    """True if ``lock_path`` is held by a live process other than the current one."""
    p = Path(lock_path)
    if not p.is_file():
        return False
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid = int(info.get("pid") or 0)
    if pid == os.getpid():
        return False
    return _pid_alive(pid)


@contextmanager
def project_thread_lock(project_id: str) -> Iterator[None]:
    """Exclusive in-process lock for all project I/O (read + write)."""
    tlock = _project_thread_lock(project_id)
    tlock.acquire()
    try:
        yield
    finally:
        tlock.release()


@contextmanager
def project_write_lock(project_id: str, lock_path: str | Path) -> Iterator[None]:
    """Combined thread-exclusive lock + cross-process conflict detection.

    Intra-process writes are serialized via ``RLock``.  Cross-process exclusion
    is enforced by :class:`ProjectFileLock` at ``open_project`` time; writers
    check ``is_locked`` to avoid clobbering a project opened elsewhere.
    """
    tlock = _project_thread_lock(project_id)
    tlock.acquire()
    try:
        if is_locked(lock_path):
            raise StorageLockError(project_id)
        yield
    finally:
        tlock.release()
