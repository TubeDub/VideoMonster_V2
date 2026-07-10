"""
VideoMonster V2 — System API
Проверка готовности системы: FFmpeg, Whisper, TTS, перевод, Python-пакеты, диск.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from pathlib import Path

from flask import Blueprint, jsonify

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("system_api", __name__)


def _has(pkg: str) -> bool:
    """Быстрая проверка наличия Python-пакета."""
    return importlib.util.find_spec(pkg) is not None


@bp.get("/api/system/check")
def api_system_check():
    """
    Возвращает полный статус системы:
    - ffmpeg        — FFmpeg в PATH
    - whisper       — faster-whisper или whisper
    - tts           — edge-tts
    - translation   — deep-translator или googletrans
    - pydub         — pydub (для Timing Engine)
    - langdetect    — langdetect
    - disk_gb       — свободное место (ГБ)
    - python_ver    — версия Python
    - ready         — всё обязательное установлено
    """
    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_ok   = bool(ffmpeg_path)

    whisper_ok  = _has("faster_whisper") or _has("whisper")
    tts_ok      = _has("edge_tts")
    transl_ok   = (
        _has("deep_translator")
        or _has("googletrans")
        or _has("argostranslate")
    )
    pydub_ok    = _has("pydub")
    langdet_ok  = _has("langdetect")

    try:
        usage    = shutil.disk_usage(str(APP_DIR))
        disk_gb  = round(usage.free / 1024**3, 1)
    except Exception:
        disk_gb  = -1

    required_ok = ffmpeg_ok and tts_ok

    try:
        from engines.hardware_probe import probe_hardware

        hardware = probe_hardware()
    except Exception:
        hardware = {}

    checks = [
        {
            "id":      "ffmpeg",
            "label":   "FFmpeg",
            "ok":      ffmpeg_ok,
            "hint":    "ffmpeg.org — скачайте и добавьте в PATH" if not ffmpeg_ok else "",
            "critical": True,
        },
        {
            "id":      "tts",
            "label":   "Edge-TTS (озвучка)",
            "ok":      tts_ok,
            "hint":    "pip install edge-tts" if not tts_ok else "",
            "critical": True,
        },
        {
            "id":      "whisper",
            "label":   "Whisper (авто-дубляж)",
            "ok":      whisper_ok,
            "hint":    "pip install faster-whisper" if not whisper_ok else "",
            "critical": False,
        },
        {
            "id":      "translation",
            "label":   "Переводчик (Argos / deep-translator)",
            "ok":      transl_ok,
            "hint":    "pip install deep-translator" if not transl_ok else "",
            "critical": False,
        },
        {
            "id":      "pydub",
            "label":   "Pydub (тайминг)",
            "ok":      pydub_ok,
            "hint":    "pip install pydub" if not pydub_ok else "",
            "critical": False,
        },
        {
            "id":      "langdetect",
            "label":   "Langdetect (автоязык)",
            "ok":      langdet_ok,
            "hint":    "pip install langdetect" if not langdet_ok else "",
            "critical": False,
        },
    ]

    try:
        from engines.model_cache import cache_status

        mc = cache_status(APP_DIR)
    except Exception:
        mc = {}

    return jsonify({
        "ready":      required_ok,
        "checks":     checks,
        "disk_gb":    disk_gb,
        "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform":   platform.system(),
        "ffmpeg_path": ffmpeg_path or "",
        "hardware":   hardware,
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
    ffmpeg_ok = bool(shutil.which("ffmpeg"))
    tts_ok    = _has("edge_tts")
    whisper   = _has("faster_whisper") or _has("whisper")

    if ffmpeg_ok and tts_ok and whisper:
        level   = "ok"
        message = "Всё готово к работе"
    elif ffmpeg_ok and tts_ok:
        level   = "warn"
        message = "Готово (авто-дубляж недоступен — установите Whisper)"
    elif not ffmpeg_ok:
        level   = "error"
        message = "FFmpeg не установлен — часть функций недоступна"
    else:
        level   = "warn"
        message = "Некоторые компоненты отсутствуют"

    return jsonify({"level": level, "message": message,
                    "ffmpeg": ffmpeg_ok, "tts": tts_ok, "whisper": whisper})
