"""OpenDDF Analyzer 2.0 — API and export (no pipeline changes)."""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

logger = logging.getLogger("tubedub.openddf_analyzer_api")

bp = Blueprint("openddf_analyzer_api", __name__)

APP_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = APP_DIR / "output"


def _asksaveasfilename(**kwargs) -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    dest = filedialog.asksaveasfilename(parent=root, **kwargs)
    root.destroy()
    return dest or None


def _task_info_for_id(task_id: str) -> dict | None:
    from api.auto_dub_api import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return None
        info = dict(task.get("info") or {})
        info.setdefault("task_id", task_id)
        return info


@bp.get("/dev/openddf-analyzer")
@bp.get("/dev/openddf-analyzer/<task_id>")
def page_openddf_analyzer(task_id: str | None = None):
    from flask import render_template

    return render_template(
        "openddf_analyzer.html",
        task_id=task_id or "",
    )


@bp.get("/api/openddf_analyzer/report/<task_id>")
def api_analyzer_report(task_id: str):
    from engines.openddf_analyzer_report import build_analyzer_v2_report

    info = _task_info_for_id(task_id)
    if info is None:
        return jsonify({"ok": False, "error": "Задача не найдена"}), 404

    if info.get("openddf_full_report"):
        raw = dict(info["openddf_full_report"])
        raw["task_info"] = info
    else:
        raw = info

    report = build_analyzer_v2_report(raw, app_dir=APP_DIR)
    return jsonify({"ok": True, "report": report, "task_id": task_id})


@bp.post("/api/openddf_analyzer/load_json")
def api_analyzer_load_json():
    from engines.openddf_analyzer_report import build_analyzer_v2_report

    if request.is_json:
        raw = request.get_json(silent=True) or {}
    else:
        upload = request.files.get("file")
        if not upload:
            return jsonify({"ok": False, "error": "Файл не передан"}), 400
        try:
            raw = json.loads(upload.read().decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return jsonify({"ok": False, "error": f"Невалидный JSON: {exc}"}), 400

    report = build_analyzer_v2_report(raw if isinstance(raw, dict) else {}, app_dir=APP_DIR)
    return jsonify({"ok": True, "report": report})


@bp.get("/api/openddf_analyzer/audio/<task_id>/<int:segment_index>/<kind>")
def api_analyzer_audio(task_id: str, segment_index: int, kind: str):
    info = _task_info_for_id(task_id)
    if info is None:
        return jsonify({"error": "task not found"}), 404

    segments = info.get("segments_data") or []
    if segment_index < 0 or segment_index >= len(segments):
        return jsonify({"error": "segment not found"}), 404

    seg = segments[segment_index]
    path: str | None = None
    if kind == "original":
        path = info.get("original_audio_path") or info.get("audio_path")
    elif kind == "tts":
        path = seg.get("file") or seg.get("tts_file_path")
    elif kind in ("fitted", "final"):
        path = seg.get("fitted_file") or seg.get("file")
    else:
        return jsonify({"error": "invalid kind"}), 400

    if not path:
        return jsonify({"error": "no audio path"}), 404

    p = Path(path)
    if not p.is_file():
        p = OUTPUT_DIR / path
    if not p.is_file():
        p = APP_DIR / "output" / str(path)
    if not p.is_file():
        return jsonify({"error": "file missing"}), 404

    return send_file(p, mimetype="audio/mpeg", conditional=True)


@bp.post("/api/openddf_analyzer/export/<export_kind>/save")
def api_analyzer_export_save(export_kind: str):
    """Save As dialog for JSON / HTML / PDF / ZIP exports."""
    payload = request.get_json(silent=True) or {}
    report = payload.get("report")
    if not isinstance(report, dict):
        return jsonify({"error": "report required"}), 400

    task_id = str(report.get("task_id") or "openddf")
    from engines.openddf_analyzer_report import export_analyzer_html

    defaults = {
        "json": (f"openddf_analyzer_{task_id}.json", [("JSON", "*.json")], ".json"),
        "html": (f"openddf_analyzer_{task_id}.html", [("HTML", "*.html")], ".html"),
        "pdf": (f"openddf_analyzer_{task_id}.pdf", [("PDF", "*.pdf")], ".pdf"),
        "zip": (f"openddf_analyzer_{task_id}.zip", [("ZIP", "*.zip")], ".zip"),
    }
    if export_kind not in defaults:
        return jsonify({"error": "invalid export kind"}), 400

    default_name, filetypes, ext = defaults[export_kind]

    try:
        dest = _asksaveasfilename(
            title=f"Сохранить OpenDDF Analyzer ({export_kind.upper()})",
            defaultextension=ext,
            initialfile=default_name,
            filetypes=filetypes + [("All files", "*.*")],
        )
    except Exception as exc:
        return jsonify({"error": f"Диалог сохранения недоступен: {exc}"}), 500

    if not dest:
        return jsonify({"cancelled": True})

    dest_path = Path(dest)
    if dest_path.suffix.lower() != ext:
        dest_path = dest_path.with_suffix(ext)

    try:
        if export_kind == "json":
            dest_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        elif export_kind == "html":
            dest_path.write_text(export_analyzer_html(report), encoding="utf-8")
        elif export_kind == "pdf":
            html_path = dest_path.with_suffix(".html")
            html_path.write_text(export_analyzer_html(report), encoding="utf-8")
            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.pdfgen import canvas as pdf_canvas

                c = pdf_canvas.Canvas(str(dest_path), pagesize=A4)
                y = 800
                c.setFont("Helvetica", 10)
                for line in json.dumps(report.get("statistics", {}), ensure_ascii=False).splitlines():
                    c.drawString(40, y, line[:110])
                    y -= 14
                    if y < 40:
                        c.showPage()
                        y = 800
                c.save()
            except ImportError:
                shutil.copy2(html_path, dest_path.with_suffix(".html"))
                return jsonify(
                    {
                        "success": True,
                        "path": str(html_path.resolve()),
                        "filename": html_path.name,
                        "note": "reportlab не установлен — сохранён HTML для печати в PDF",
                    }
                )
        elif export_kind == "zip":
            with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "analyzer_report.json",
                    json.dumps(report, ensure_ascii=False, indent=2),
                )
                zf.writestr("analyzer_report.html", export_analyzer_html(report))
    except OSError as exc:
        return jsonify({"error": f"Ошибка записи: {exc}"}), 500

    logger.info("[Analyzer-Export] kind=%s path=%s", export_kind, dest_path)
    return jsonify(
        {
            "success": True,
            "path": str(dest_path.resolve()),
            "filename": dest_path.name,
        }
    )
