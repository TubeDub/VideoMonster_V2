"""TubeDub first-run preparation — per-component progress + install log."""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path
from typing import Any

from engines.install_log import install_log

logger = logging.getLogger("tubedub.system_prepare")

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
PREPARED_MARKER = DATA_DIR / ".tubedub_prepared"

_READY = "ready"
_PENDING = "pending"
_OPTIONAL = "optional"
_ERROR = "error"
_ACTIVE = "active"

_PREPARE_LOCK = threading.Lock()
_PREPARE_THREAD: threading.Thread | None = None
_PREPARE_STARTED_AT = 0.0
_STATE: dict[str, Any] = {
    "running": False,
    "done": False,
    "ready": False,
    "error": None,
    "wait_reason": "",
    "overall_percent": 0,
    "current_component": "",
    "components": [],
}


def _default_components() -> list[dict[str, Any]]:
    return [
        {"id": "python", "label": "Python", "status": _PENDING, "percent": 0, "message": "Ожидание…"},
        {"id": "ffmpeg", "label": "FFmpeg", "status": _PENDING, "percent": 0, "message": "Ожидание…"},
        {"id": "whisper", "label": "Whisper", "status": _PENDING, "percent": 0, "message": "Ожидание…"},
        {"id": "marian", "label": "Marian MT", "status": _PENDING, "percent": 0, "message": "Ожидание…"},
        {"id": "ollama", "label": "Ollama / Qwen", "status": _PENDING, "percent": 0, "message": "Ожидание…"},
        {"id": "verify", "label": "Проверка", "status": _PENDING, "percent": 0, "message": "Ожидание…"},
    ]


def _set_component(
    comp_id: str,
    *,
    status: str,
    percent: float,
    message: str,
) -> None:
    for c in _STATE["components"]:
        if c.get("id") == comp_id:
            c["status"] = status
            c["percent"] = round(max(0.0, min(100.0, float(percent))), 1)
            c["message"] = message
            break
    _STATE["current_component"] = comp_id
    done = sum(float(c.get("percent") or 0) for c in _STATE["components"])
    _STATE["overall_percent"] = round(done / max(len(_STATE["components"]), 1), 1)
    elapsed = time.time() - _PREPARE_STARTED_AT if _PREPARE_STARTED_AT else 0
    if elapsed >= 30 and status == _ACTIVE:
        _STATE["wait_reason"] = message
    install_log(
        f"{comp_id}: {percent:.0f}% — {message}",
        component=comp_id,
    )


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _step_python() -> None:
    _set_component("python", status=_ACTIVE, percent=10, message="Проверка среды Python…")
    import sys

    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    _set_component(
        "python",
        status=_READY,
        percent=100,
        message=f"Python {ver} готов",
    )


def _step_ffmpeg() -> None:
    _set_component("ffmpeg", status=_ACTIVE, percent=5, message="Поиск FFmpeg…")
    try:
        from engines.ffmpeg_paths import find_ffmpeg, find_ffprobe

        ff = find_ffmpeg()
        probe = find_ffprobe()
        if ff:
            _set_component(
                "ffmpeg",
                status=_READY,
                percent=100,
                message="FFmpeg найден" + (" (ffprobe ✓)" if probe else ""),
            )
            return
        _set_component(
            "ffmpeg",
            status=_ERROR,
            percent=0,
            message="FFmpeg не найден — положите ffmpeg.exe в папку программы",
        )
    except Exception as exc:
        _set_component("ffmpeg", status=_ERROR, percent=0, message=str(exc)[:120])


def _step_whisper() -> None:
    _set_component("whisper", status=_ACTIVE, percent=10, message="Проверка Whisper (faster-whisper)…")
    if _has_module("faster_whisper") or _has_module("whisper"):
        _set_component("whisper", status=_READY, percent=100, message="Whisper установлен")
        return
    _set_component(
        "whisper",
        status=_OPTIONAL,
        percent=0,
        message="Пакет Whisper не найден — загрузится при первом дубляже",
    )


def _step_marian() -> None:
    _set_component("marian", status=_ACTIVE, percent=10, message="Проверка Marian MT (Transformers)…")
    if _has_module("transformers"):
        _set_component("marian", status=_READY, percent=100, message="Marian MT (Transformers) готов")
        return
    _set_component(
        "marian",
        status=_OPTIONAL,
        percent=0,
        message="Transformers не установлен — модель загрузится при первом переводе",
    )


def _step_ollama() -> None:
    _set_component("ollama", status=_ACTIVE, percent=5, message="Проверка Ollama / локальной LLM…")
    deadline = time.time() + 180.0
    while time.time() < deadline:
        try:
            from engines.ai_manager.config import (
                STATUS_INSTALLING,
                STATUS_READY,
                load_config,
            )

            cfg = load_config(APP_DIR)
            status = str(cfg.get("status") or "")
            model = str(cfg.get("model") or "qwen2.5:14b")
            prog = cfg.get("install_progress") or {}

            if status == STATUS_INSTALLING:
                pct = float(prog.get("percent") or 0)
                msg = str(prog.get("message") or "Установка AI-модуля…")
                phase = str(prog.get("phase") or "")
                detail = f"{msg} ({phase})" if phase else msg
                _set_component(
                    "ollama",
                    status=_ACTIVE,
                    percent=max(5.0, pct),
                    message=f"{detail} — модель {model}",
                )
                time.sleep(2.0)
                continue

            from engines.ai_manager.installer import find_ollama_binary

            if find_ollama_binary() is None:
                _set_component(
                    "ollama",
                    status=_OPTIONAL,
                    percent=0,
                    message=f"Ollama не установлен — Qwen ({model}) можно поставить в настройках",
                )
                return

            _set_component("ollama", status=_ACTIVE, percent=40, message="Запуск Ollama (headless)…")
            try:
                from engines.llm_adaptation_mode import discover_local_llm

                found = discover_local_llm(force=False)
                if found:
                    models = found.get("models") or []
                    name = models[0] if models else model
                    _set_component(
                        "ollama",
                        status=_READY,
                        percent=100,
                        message=f"Ollama отвечает — {name}",
                    )
                    return
            except Exception:
                pass

            if status == STATUS_READY:
                _set_component(
                    "ollama",
                    status=_READY,
                    percent=100,
                    message=f"AI-модуль готов ({model})",
                )
            else:
                _set_component(
                    "ollama",
                    status=_OPTIONAL,
                    percent=50,
                    message="Ollama установлен, сервер запускается в фоне…",
                )
            return
        except Exception as exc:
            _set_component("ollama", status=_OPTIONAL, percent=0, message=str(exc)[:120])
            return
    _set_component(
        "ollama",
        status=_OPTIONAL,
        percent=0,
        message="Таймаут ожидания Ollama — продолжите или см. logs/install.log",
    )


def _step_verify() -> None:
    _set_component("verify", status=_ACTIVE, percent=50, message="Финальная проверка…")
    essential = ("ffmpeg",)
    errors = [
        c for c in _STATE["components"]
        if c.get("id") in essential and c.get("status") == _ERROR
    ]
    if errors:
        msg = errors[0].get("message") or "Ошибка проверки"
        _set_component("verify", status=_ERROR, percent=0, message=msg)
        _STATE["error"] = msg
        _STATE["ready"] = False
        return
    _set_component("verify", status=_READY, percent=100, message="Готово")
    _STATE["ready"] = True
    _STATE["error"] = None


def _run_prepare() -> None:
    global _PREPARE_STARTED_AT
    _PREPARE_STARTED_AT = time.time()
    _STATE["running"] = True
    _STATE["done"] = False
    _STATE["components"] = _default_components()
    _STATE["wait_reason"] = ""
    _STATE["error"] = None
    install_log("=== TubeDub prepare started ===", component="prepare")

    try:
        _step_python()
        _step_ffmpeg()
        _step_whisper()
        _step_marian()
        _step_ollama()
        _step_verify()
    except Exception as exc:
        logger.exception("prepare failed")
        _STATE["error"] = str(exc)[:200]
        install_log(f"prepare failed: {exc}", level="error", component="prepare")
    finally:
        _STATE["running"] = False
        _STATE["done"] = True
        install_log(
            f"=== prepare finished ready={_STATE.get('ready')} error={_STATE.get('error') or '-'} ===",
            component="prepare",
        )


def start_background_prepare(*, force: bool = False) -> None:
    """Start preparation once (non-blocking)."""
    global _PREPARE_THREAD
    if PREPARED_MARKER.exists() and not force:
        return
    with _PREPARE_LOCK:
        if _PREPARE_THREAD and _PREPARE_THREAD.is_alive():
            return
        if _STATE.get("done") and not force:
            return
        _PREPARE_THREAD = threading.Thread(
            target=_run_prepare,
            name="tubedub-prepare",
            daemon=True,
        )
        _PREPARE_THREAD.start()


def ensure_ai_headless() -> None:
    """Deprecated for API polls — kept for compatibility; no-op (app.py runs in background)."""
    return


def get_prepare_status() -> dict[str, Any]:
    """Fast status for UI polling — never blocks on Ollama verify."""
    elapsed = round(time.time() - _PREPARE_STARTED_AT, 1) if _PREPARE_STARTED_AT else 0
    components = _STATE.get("components") or _default_components()
    if not components:
        components = _default_components()

    essential_ready = not any(
        c.get("status") == _ERROR for c in components if c.get("id") in ("ffmpeg",)
    )
    ready = bool(_STATE.get("ready")) and essential_ready
    error = _STATE.get("error")
    running = bool(_STATE.get("running"))

    # Stuck guard: if prepare never started, kick it.
    if not running and not _STATE.get("done") and not PREPARED_MARKER.exists():
        start_background_prepare()

    # Long wait hint for UI.
    wait_reason = str(_STATE.get("wait_reason") or "")
    if not wait_reason and elapsed >= 45 and running:
        cur = str(_STATE.get("current_component") or "")
        wait_reason = f"Дольше обычного: проверяем {cur or 'компоненты'}…"

    if elapsed >= 120 and running:
        wait_reason = (
            wait_reason
            or "Установка занимает больше 2 минут — смотрите logs/install.log"
        )

    return {
        "ok": error is None,
        "ready": ready,
        "running": running,
        "done": bool(_STATE.get("done")),
        "already_prepared": PREPARED_MARKER.exists(),
        "elapsed_sec": elapsed,
        "overall_percent": _STATE.get("overall_percent", 0),
        "current_component": _STATE.get("current_component", ""),
        "wait_reason": wait_reason,
        "error": error,
        "log_path": "logs/install.log",
        "components": components,
    }


def mark_prepared() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PREPARED_MARKER.write_text(f"prepared_at={time.time()}\n", encoding="utf-8")
        install_log("User dismissed prepare screen", component="prepare")
    except Exception as exc:
        logger.debug("mark_prepared: %s", exc)


def reset_prepared() -> None:
    try:
        if PREPARED_MARKER.exists():
            PREPARED_MARKER.unlink()
        _STATE.update(
            {
                "running": False,
                "done": False,
                "ready": False,
                "error": None,
                "components": [],
            }
        )
    except Exception:
        pass
