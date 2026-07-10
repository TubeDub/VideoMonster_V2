"""Background build jobs for owner panel (ecosystem + test EXE)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}


def _set(job_id: str, **fields: Any) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(fields)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(kind: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        items = list(_JOBS.values())
    if kind:
        items = [j for j in items if j.get("kind") == kind]
    return sorted(items, key=lambda x: x.get("started_at", 0), reverse=True)


def start_ecosystem_build() -> str:
    job_id = uuid.uuid4().hex[:12]

    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": "ecosystem",
            "status": "running",
            "progress": 0,
            "message": "Запуск сборки экосистемы…",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def _worker() -> None:
        try:
            _set(job_id, progress=5, message="PyInstaller: 7 приложений…")
            from engines.ecosystem_installer import build_and_install_ecosystem

            result = build_and_install_ecosystem()
            if result.get("ok"):
                _set(
                    job_id,
                    status="done",
                    progress=100,
                    message=result.get("message", "Экосистема установлена"),
                    finished_at=time.time(),
                    result=result,
                )
            else:
                _set(
                    job_id,
                    status="error",
                    progress=100,
                    message="Сборка завершилась с ошибками",
                    finished_at=time.time(),
                    error=str(result.get("missing") or result.get("build_message")),
                    result=result,
                )
        except Exception as e:
            logger.exception("Ecosystem build job failed: %s", e)
            _set(
                job_id,
                status="error",
                progress=100,
                message=str(e),
                finished_at=time.time(),
                error=str(e),
            )

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def start_test_exe_build(key_type: str = "TEST-7", label: str = "") -> str:
    job_id = uuid.uuid4().hex[:12]

    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": "test_exe",
            "status": "running",
            "progress": 0,
            "message": f"Сборка EXE ({key_type})…",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def _worker() -> None:
        try:
            _set(job_id, progress=10, message="Подготовка staging…")
            from engines.test_build_manager import create_test_exe_build

            result = create_test_exe_build(key_type, label=label)
            if result.get("ok"):
                _set(
                    job_id,
                    status="done",
                    progress=100,
                    message=result.get("message", "EXE готов"),
                    finished_at=time.time(),
                    result=result,
                )
            else:
                _set(
                    job_id,
                    status="error",
                    progress=100,
                    message=result.get("error", "Ошибка сборки"),
                    finished_at=time.time(),
                    error=result.get("error"),
                    result=result,
                )
        except Exception as e:
            logger.exception("Test EXE build job failed: %s", e)
            _set(
                job_id,
                status="error",
                progress=100,
                message=str(e),
                finished_at=time.time(),
                error=str(e),
            )

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def start_setup_installer_build(*, test_build: bool = False, key_type: str = "TEST-7", label: str = "") -> str:
    job_id = uuid.uuid4().hex[:12]
    kind = "test_setup" if test_build else "production_setup"
    title = "TubeDub_Test_7_Days_Setup.exe" if test_build else "TubeDub_Setup.exe"

    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "progress": 0,
            "message": f"Сборка {title}…",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def _worker() -> None:
        try:
            _set(job_id, progress=10, message="PyInstaller + Inno Setup…")
            from engines.installer_builder import create_setup_installer

            result = create_setup_installer(
                test_build=test_build, key_type=key_type, label=label
            )
            if result.get("ok"):
                _set(
                    job_id,
                    status="done",
                    progress=100,
                    message=result.get("message", "Установщик готов"),
                    finished_at=time.time(),
                    result=result,
                )
            else:
                _set(
                    job_id,
                    status="error",
                    progress=100,
                    message=result.get("error", "Ошибка сборки"),
                    finished_at=time.time(),
                    error=result.get("error"),
                    result=result,
                )
        except Exception as e:
            logger.exception("Setup installer job failed: %s", e)
            _set(
                job_id,
                status="error",
                progress=100,
                message=str(e),
                finished_at=time.time(),
                error=str(e),
            )

    threading.Thread(target=_worker, daemon=True).start()
    return job_id
