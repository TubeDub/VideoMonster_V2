"""
VideoMonster V2 — System API
Проверка готовности системы: FFmpeg, Whisper, TTS, перевод, Python-пакеты, диск.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import platform
import shutil
import sys
from pathlib import Path

from flask import Blueprint, jsonify

logger = logging.getLogger("tubedub.api.system")

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("system_api", __name__)


def _has(pkg: str) -> bool:
    """Быстрая проверка наличия Python-пакета.

    ``find_spec`` может импортировать родительский пакет и бросить любое
    исключение (ImportError/ValueError/ModuleNotFoundError и др.) на холодном
    старте — глушим всё, чтобы статус-эндпоинт никогда не падал в 500.
    """
    try:
        return importlib.util.find_spec(pkg) is not None
    except BaseException:  # noqa: BLE001 — probe must never raise
        return False


def _disk_free_gb(path: Path) -> float:
    try:
        # On Windows resolve to an existing drive root if needed.
        probe = path if path.exists() else path.anchor or str(path)
        usage = shutil.disk_usage(str(probe))
        return round(usage.free / 1024**3, 1)
    except OSError as exc:
        logger.warning("disk_usage failed for %s: %s", path, exc)
        return -1.0


@bp.get("/api/system/check")
def api_system_check():
    """System status endpoint — must NEVER return 500.

    Any transient failure (cold-start import race, disk probe, model-cache scan)
    degrades gracefully to ``ready=false`` instead of a 500 that would break the
    UI readiness gate.
    """
    try:
        return _system_check_impl()
    except Exception as exc:  # noqa: BLE001 — status must always answer
        logger.exception("system_check failed, returning degraded status: %s", exc)
        return jsonify({
            "ok": False,
            "ready": False,
            "error": str(exc),
            "checks": [],
            "disk_gb": _disk_free_gb(APP_DIR),
            "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
        })


def _system_check_impl():
    """
    Возвращает полный статус системы:
    - ffmpeg / ffprobe
    - whisper, tts, translation, pydub, langdetect
    - disk_gb, python_ver, hardware, model_cache
    - ready — всё обязательное установлено
    """
    try:
        from engines.ffmpeg_paths import find_ffmpeg, find_ffprobe

        ffmpeg_path = find_ffmpeg() or shutil.which("ffmpeg") or ""
        ffprobe_path = find_ffprobe() or shutil.which("ffprobe") or ""
    except Exception:
        ffmpeg_path = shutil.which("ffmpeg") or ""
        ffprobe_path = shutil.which("ffprobe") or ""

    ffmpeg_ok = bool(ffmpeg_path)
    ffprobe_ok = bool(ffprobe_path)

    whisper_ok = _has("faster_whisper") or _has("whisper")
    tts_ok = _has("edge_tts")
    transl_ok = (
        _has("deep_translator")
        or _has("googletrans")
        or _has("argostranslate")
    )
    offline_mt_runtime = _has("torch") and _has("transformers")
    argos_pkg = _has("argostranslate")
    if offline_mt_runtime or argos_pkg:
        offline_mt_ok = True
        if offline_mt_runtime and argos_pkg:
            offline_mt_hint = ""
        elif offline_mt_runtime:
            offline_mt_hint = (
                "Argos не установлен — офлайн-пары зависят от Marian/NLLB весов."
            )
        else:
            offline_mt_hint = (
                "Marian/NLLB недоступны (нет torch/transformers). "
                "Доступен только Argos, если пакеты языков установлены."
            )
    else:
        offline_mt_ok = False
        offline_mt_hint = (
            "Офлайн-MT недоступен: нет torch/transformers и argostranslate. "
            "Установите зависимости или используйте онлайн deep-translator."
        )

    pydub_ok = _has("pydub")
    langdet_ok = _has("langdetect")

    # Piper offline TTS probe
    piper_pkg = _has("piper") or _has("piper_tts")
    piper_cli = bool(shutil.which("piper"))
    piper_model = (
        (os.getenv("PIPER_MODEL") or os.getenv("VM_PIPER_MODEL") or "").strip()
    )
    piper_model_ok = bool(piper_model and Path(piper_model).is_file())
    piper_ok = (piper_pkg or piper_cli) and piper_model_ok
    if piper_ok:
        piper_hint = ""
    elif not (piper_pkg or piper_cli):
        piper_hint = (
            "Piper не установлен (pip install piper-tts или CLI piper в PATH). "
            "Офлайн-озвучка: edge-tts или Piper + модель."
        )
    else:
        piper_hint = (
            "Piper найден, но нет модели: задайте PIPER_MODEL / VM_PIPER_MODEL "
            "(путь к .onnx)."
        )

    disk_gb = _disk_free_gb(APP_DIR)
    required_ok = ffmpeg_ok and tts_ok

    try:
        from engines.hardware_probe import probe_hardware

        hardware = probe_hardware()
    except Exception:
        hardware = {}

    checks = [
        {
            "id": "ffmpeg",
            "label": "FFmpeg",
            "ok": ffmpeg_ok,
            "hint": "ffmpeg.org — скачайте и добавьте в PATH" if not ffmpeg_ok else "",
            "critical": True,
            "path": ffmpeg_path or "",
        },
        {
            "id": "ffprobe",
            "label": "FFprobe",
            "ok": ffprobe_ok,
            "hint": "Обычно идёт в комплекте с FFmpeg" if not ffprobe_ok else "",
            "critical": False,
            "path": ffprobe_path or "",
        },
        {
            "id": "tts",
            "label": "Edge-TTS (озвучка)",
            "ok": tts_ok,
            "hint": "pip install edge-tts" if not tts_ok else "",
            "critical": True,
        },
        {
            "id": "piper_tts",
            "label": "Piper TTS (офлайн)",
            "ok": piper_ok,
            "hint": piper_hint,
            "critical": False,
            "cli": piper_cli,
            "package": piper_pkg,
            "model": piper_model_ok,
        },
        {
            "id": "whisper",
            "label": "Whisper (авто-дубляж)",
            "ok": whisper_ok,
            "hint": "pip install faster-whisper" if not whisper_ok else "",
            "critical": False,
        },
        {
            "id": "translation",
            "label": "Переводчик (Argos / deep-translator)",
            "ok": transl_ok,
            "hint": "pip install deep-translator" if not transl_ok else "",
            "critical": False,
        },
        {
            "id": "offline_mt",
            "label": "Офлайн-MT runtime (torch/transformers или Argos)",
            "ok": offline_mt_ok,
            "hint": offline_mt_hint,
            "critical": False,
            "torch": _has("torch"),
            "transformers": _has("transformers"),
            "argostranslate": argos_pkg,
        },
        {
            "id": "pydub",
            "label": "Pydub (тайминг)",
            "ok": pydub_ok,
            "hint": "pip install pydub" if not pydub_ok else "",
            "critical": False,
        },
        {
            "id": "langdetect",
            "label": "Langdetect (автоязык)",
            "ok": langdet_ok,
            "hint": "pip install langdetect" if not langdet_ok else "",
            "critical": False,
        },
    ]

    try:
        from engines.model_cache import cache_status

        mc = cache_status(APP_DIR)
    except Exception:
        mc = {}

    version = {}
    try:
        from engines.app_version import version_info

        version = version_info()
    except Exception:
        version = {}

    try:
        from engines.app_loader import heavy_blueprint_status

        heavy = heavy_blueprint_status()
    except Exception:
        heavy = {"loaded": False, "failures": [], "degraded": False}

    ready = required_ok and not heavy.get("degraded") and not heavy.get("core_degraded")

    return jsonify({
        "ok": True,
        "ready": ready,
        "checks": checks,
        "disk_gb": disk_gb,
        "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.system(),
        "platform_release": platform.release(),
        "ffmpeg_path": ffmpeg_path or "",
        "ffprobe_path": ffprobe_path or "",
        "hardware": hardware,
        "app": version,
        "heavy_blueprints": heavy,
        "model_cache": {
            "cache_gb": mc.get("cache_gb", 0),
            "max_cache_gb": mc.get("max_cache_gb", 10),
            "model_count": mc.get("model_count", 0),
            "hf_home": mc.get("hf_home", ""),
        },
    })


@bp.get("/api/system/quick_status")
def api_quick_status():
    """Быстрый светофор — только ok/warn/error."""
    try:
        from engines.ffmpeg_paths import find_ffmpeg

        ffmpeg_ok = bool(find_ffmpeg() or shutil.which("ffmpeg"))
    except Exception:
        ffmpeg_ok = bool(shutil.which("ffmpeg"))
    tts_ok = _has("edge_tts")
    whisper = _has("faster_whisper") or _has("whisper")

    if ffmpeg_ok and tts_ok and whisper:
        level = "ok"
        message = "Всё готово к работе"
    elif ffmpeg_ok and tts_ok:
        level = "warn"
        message = "Готово (авто-дубляж недоступен — установите Whisper)"
    elif not ffmpeg_ok:
        level = "error"
        message = "FFmpeg не установлен — часть функций недоступна"
    else:
        level = "warn"
        message = "Некоторые компоненты отсутствуют"

    return jsonify({
        "ok": True,
        "level": level,
        "message": message,
        "ffmpeg": ffmpeg_ok,
        "tts": tts_ok,
        "whisper": whisper,
        "disk_gb": _disk_free_gb(APP_DIR),
    })
