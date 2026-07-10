"""OCR API — отдельно от дубляжа речи. OCR по умолчанию выключен."""

from __future__ import annotations

import uuid
from pathlib import Path
from threading import RLock

from flask import Blueprint, jsonify, request, send_file

bp = Blueprint("ocr_api", __name__)
APP_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_OCR_JOBS: dict[str, dict] = {}
_LOCK = RLock()


def _resolve_video(raw_path: str) -> Path | None:
    candidates = [
        Path(raw_path),
        APP_DIR / raw_path,
        Path("uploads") / Path(raw_path).name,
        APP_DIR / "uploads" / "imports" / Path(raw_path).name,
        OUTPUT_DIR / Path(raw_path).name,
    ]
    return next((p for p in candidates if p.exists()), None)


@bp.get("/api/ocr/status")
def api_ocr_status():
    from engines.ocr_engine import ocr_available

    ok, note = ocr_available()
    return jsonify(
        {
            "available": ok,
            "detail": note,
            "default_enabled": False,
            "note": "OCR не участвует в авто-дубляже речи",
        }
    )


@bp.post("/api/ocr/extract")
def api_ocr_extract():
    """Извлечь текст с видео (явный запрос, не часть дубляжа)."""
    from engines.ocr_engine import extract_video_text

    data = request.get_json(silent=True) or {}
    raw = (data.get("video_path") or "").strip()
    if not raw:
        return jsonify({"error": "video_path required"}), 400

    vp = _resolve_video(raw)
    if not vp:
        return jsonify({"error": f"File not found: {raw}"}), 404

    enabled = bool(data.get("enabled", True))
    interval = float(data.get("sample_interval_sec") or 2.0)
    lang = (data.get("lang") or "eng").strip()

    job_id = uuid.uuid4().hex[:12]
    result = extract_video_text(
        str(vp.resolve()),
        enabled=enabled,
        sample_interval_sec=interval,
        lang=lang,
    )

    with _LOCK:
        _OCR_JOBS[job_id] = {"result": result, "video": str(vp)}

    return jsonify({"job_id": job_id, **result})


@bp.get("/api/ocr/export/<job_id>")
def api_ocr_export(job_id: str):
    """Экспорт OCR-текста (txt / srt / json)."""
    from engines.ocr_engine import export_ocr_text

    fmt = (request.args.get("format") or "txt").strip().lower()
    with _LOCK:
        job = _OCR_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    body = export_ocr_text(job["result"], fmt=fmt)
    ext = "json" if fmt == "json" else ("srt" if fmt == "srt" else "txt")
    out = OUTPUT_DIR / f"ocr_{job_id}.{ext}"
    out.write_text(body, encoding="utf-8")
    return send_file(out, as_attachment=True, download_name=out.name)


@bp.post("/api/ocr/voice")
def api_ocr_voice():
    """Озвучить OCR-текст (отдельно от дубляжа речи)."""
    from engines.tts import generate_audio

    data = request.get_json(silent=True) or {}
    job_id = (data.get("job_id") or "").strip()
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or "ru-RU-DmitryNeural").strip()

    if job_id:
        with _LOCK:
            job = _OCR_JOBS.get(job_id)
        if job:
            text = text or (job["result"].get("full_text") or "")

    if not text:
        return jsonify({"error": "No OCR text to voice"}), 400

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    files = generate_audio(text=text, voice=voice, segments=lines)
    return jsonify({"ok": True, "files": files, "segments": len(lines)})
