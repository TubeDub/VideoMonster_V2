"""
VideoMonster V2 — Reader API
Загрузка VMR, обновление позиции/закладок.
Импорт файлов: TXT, SRT, EPUB, PDF, DOCX.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Blueprint, request, jsonify, abort

APP_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

bp = Blueprint("reader_api", __name__)


# ─────────────────────────────────────────────
#  Загрузка VMR
# ─────────────────────────────────────────────

@bp.post("/api/load_vmr")
def api_load_vmr():
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    if not file.filename.endswith(".vmr"):
        return jsonify({"error": "Неверный формат файла (ожидается .vmr)"}), 400
    try:
        raw = file.read().decode("utf-8")
        doc = json.loads(raw)
    except Exception as e:
        return jsonify({"error": f"Ошибка чтения VMR: {e}"}), 400
    return jsonify(doc)


@bp.post("/api/reader/update")
def api_reader_update():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    safe = Path(filename).name
    if not safe.endswith(".vmr"):
        return jsonify({"error": "Неверный файл"}), 400
    path = OUTPUT_DIR / safe
    if not path.exists():
        return jsonify({"error": "Файл не найден"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if "reading_position" in data:
            doc["reading_position"] = data["reading_position"]
        if "bookmarks" in data:
            doc["bookmarks"] = data["bookmarks"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  Импорт файлов в Reader (TXT / SRT / EPUB / PDF / DOCX)
# ─────────────────────────────────────────────

@bp.post("/api/reader/import")
def api_reader_import():
    """
    Импортирует TXT, SRT, EPUB, PDF, DOCX в Reader.
    Возвращает { text, format, title, warning? }
    """
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400

    file = request.files["file"]
    filename = file.filename or ""
    ext   = Path(filename).suffix.lower()
    title = Path(filename).stem

    supported = {".txt", ".srt", ".sub", ".epub", ".pdf", ".docx", ".doc"}
    if ext not in supported:
        return jsonify({
            "error": f"Формат '{ext}' не поддерживается. "
                     f"Поддерживаются: TXT, SRT, EPUB, PDF, DOCX"
        }), 400

    try:
        content = file.read()

        if ext == ".txt":
            text = content.decode("utf-8", errors="replace")
            return jsonify({"text": text, "format": "txt", "title": title})

        if ext in (".srt", ".sub"):
            raw  = content.decode("utf-8", errors="replace")
            text = _parse_srt_text(raw)
            return jsonify({"text": text, "format": "srt", "title": title})

        if ext == ".epub":
            text, warn = _parse_epub(content)
            resp = {"text": text, "format": "epub", "title": title}
            if warn: resp["warning"] = warn
            return jsonify(resp)

        if ext == ".pdf":
            text, warn = _parse_pdf(content)
            resp = {"text": text, "format": "pdf", "title": title}
            if warn: resp["warning"] = warn
            return jsonify(resp)

        if ext in (".docx", ".doc"):
            text, warn = _parse_docx(content)
            resp = {"text": text, "format": "docx", "title": title}
            if warn: resp["warning"] = warn
            return jsonify(resp)

    except Exception as e:
        return jsonify({"error": f"Ошибка при импорте: {e}"}), 500

    return jsonify({"error": "Неизвестный формат"}), 400


# ─────────────────────────────────────────────
#  Парсеры форматов
# ─────────────────────────────────────────────

def _parse_srt_text(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line or re.match(r"^\d{1,2}:\d{2}:\d{2}", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _parse_epub(content: bytes) -> tuple[str, str]:
    warn = ""
    try:
        import io
        import ebooklib
        from ebooklib import epub

        book  = epub.read_epub(io.BytesIO(content))
        texts: list[str] = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                html = item.get_content().decode("utf-8", errors="replace")
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    texts.append(text)
        return "\n\n".join(texts), warn

    except ImportError:
        warn = "ebooklib не установлен (pip install ebooklib) — текст не извлечён"
        return "", warn
    except Exception as e:
        return "", f"Ошибка EPUB: {e}"


def _parse_pdf(content: bytes) -> tuple[str, str]:
    warn = ""
    # Пробуем pdfplumber
    try:
        import io
        import pdfplumber
        texts: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texts.append(t)
        return "\n\n".join(texts), warn
    except ImportError:
        pass
    except Exception as e:
        warn = f"pdfplumber: {e}"

    # Fallback: PyMuPDF
    try:
        import fitz
        import io
        doc   = fitz.open(stream=content, filetype="pdf")
        texts = [page.get_text() for page in doc]
        return "\n\n".join(texts), warn
    except ImportError:
        warn = "Для PDF установите: pip install pdfplumber  или  pip install PyMuPDF"
        return "", warn
    except Exception as e:
        return "", f"Ошибка PDF: {e}"


def _parse_docx(content: bytes) -> tuple[str, str]:
    warn = ""
    try:
        import io
        import docx
        doc   = docx.Document(io.BytesIO(content))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(lines), warn
    except ImportError:
        warn = "python-docx не установлен (pip install python-docx) — текст не извлечён"
        return "", warn
    except Exception as e:
        return "", f"Ошибка DOCX: {e}"
