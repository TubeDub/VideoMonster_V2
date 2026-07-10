"""AI Assistant API stub — maps commands to naturalizer helpers (TZ §10)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.core.feature_flags import is_developer

APP_DIR = Path(__file__).resolve().parent.parent
bp = Blueprint("assistant_api", __name__)

_COMMANDS = {
    "shorter": "shorten",
    "shorten": "shorten",
    "conversational": "conversational",
    "formal": "formal",
    "polish": "polish",
}


def _dev_guard():
    if not is_developer(
        request_headers=dict(request.headers),
        request_cookies=dict(request.cookies),
    ):
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    return None


@bp.post("/api/assistant/command")
def api_assistant_command():
    blocked = _dev_guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    cmd = str(data.get("command") or "").strip().lower()
    text = str(data.get("text") or "").strip()
    lang = str(data.get("lang") or "ru").strip()
    slot_ms = int(data.get("slot_ms") or 0)

    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    if cmd not in _COMMANDS:
        return jsonify({"ok": False, "error": f"Unknown command: {cmd}", "available": list(_COMMANDS)}), 400

    result = text
    meta: dict = {"command": cmd}

    if cmd in ("shorter", "shorten"):
        from engines.soft_sync import shorten_text_for_slot

        result = shorten_text_for_slot(text, slot_ms=slot_ms or 5000, lang=lang)
        meta["method"] = "smart_segment_optimizer"
    elif cmd == "conversational":
        try:
            from engines.translation_naturalizer import naturalize_ru

            result = naturalize_ru(text) if lang.startswith("ru") else text
            meta["method"] = "naturalize_ru"
        except Exception as exc:
            meta["error"] = str(exc)
    elif cmd == "formal":
        try:
            from engines.translation_naturalizer import polish_segment_detailed

            rep = polish_segment_detailed(text, original=text, tgt_lang=lang)
            result = rep.text if hasattr(rep, "text") else str(rep)
            meta["method"] = "polish_formal"
        except Exception as exc:
            meta["error"] = str(exc)
    elif cmd == "polish":
        try:
            from engines.translation_naturalizer import polish_segment_detailed

            rep = polish_segment_detailed(text, original=text, tgt_lang=lang)
            result = rep.text if hasattr(rep, "text") else str(rep)
            meta["method"] = "polish"
        except Exception as exc:
            meta["error"] = str(exc)

    return jsonify(
        {
            "ok": True,
            "command": cmd,
            "input": text,
            "output": result,
            "changed": result != text,
            "meta": meta,
        }
    )


@bp.get("/api/assistant/commands")
def api_assistant_commands():
    blocked = _dev_guard()
    if blocked:
        return blocked
    return jsonify({"ok": True, "commands": list(_COMMANDS.keys())})
