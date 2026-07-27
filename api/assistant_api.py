"""AI Assistant API — naturalizer commands + rule-based review (TZ §10)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.core.feature_flags import is_developer, is_enabled

APP_DIR = Path(__file__).resolve().parent.parent
bp = Blueprint("assistant_api", __name__)

_COMMANDS = {
    "shorter": "shorten",
    "shorten": "shorten",
    "conversational": "conversational",
    "formal": "formal",
    "polish": "polish",
    "review": "review",
    "fix_calque": "fix_calque",
    "analyze": "analyze",
}


def _allowed() -> bool:
    if is_developer(
        request_headers=dict(request.headers),
        request_cookies=dict(request.cookies),
    ):
        return True
    try:
        return bool(
            is_enabled(
                "ai_assistant",
                developer_session=False,
                show_beta=True,
            )
        )
    except Exception:
        return False


def _guard():
    if not _allowed():
        return jsonify({"ok": False, "error": "Developer mode or FEATURE_AI_ASSISTANT required"}), 403
    return None


@bp.get("/api/assistant/health")
def api_assistant_health():
    return jsonify(
        {
            "ok": True,
            "module": "ai_assistant",
            "readiness": "GREEN",
            "commands": sorted(set(_COMMANDS.keys())),
            "allowed": _allowed(),
        }
    )


@bp.post("/api/assistant/command")
def api_assistant_command():
    blocked = _guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    cmd = str(data.get("command") or "").strip().lower()
    text = str(data.get("text") or "").strip()
    source = str(data.get("source") or data.get("original") or "").strip()
    lang = str(data.get("lang") or "ru").strip()
    slot_ms = int(data.get("slot_ms") or 0)

    if cmd not in _COMMANDS:
        return jsonify({"ok": False, "error": f"Unknown command: {cmd}", "available": list(_COMMANDS)}), 400
    if cmd not in ("analyze", "review") and not text:
        return jsonify({"ok": False, "error": "text required"}), 400

    result = text
    meta: dict = {"command": cmd}
    issues: list = []

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
    elif cmd in ("formal", "polish"):
        try:
            from engines.translation_naturalizer import polish_segment_detailed

            rep = polish_segment_detailed(text, original=source or text, tgt_lang=lang)
            result = rep.text if hasattr(rep, "text") else str(rep)
            meta["method"] = "polish_formal" if cmd == "formal" else "polish"
        except Exception as exc:
            meta["error"] = str(exc)
    elif cmd == "fix_calque":
        try:
            from engines.translation_naturalizer import naturalize_ru

            result = naturalize_ru(text) if lang.startswith("ru") else text
            # Common UK→RU junior calque
            if "молодш" in result.lower():
                result = result.replace("молодший", "младший").replace("Молодший", "Младший")
            meta["method"] = "fix_calque"
        except Exception as exc:
            meta["error"] = str(exc)
    elif cmd == "review":
        from engines.ai_assistant.analyzer import analyze_translation_review_segment

        issues = analyze_translation_review_segment(
            source=source or text,
            translated=text if source else text,
            router_reason=str(data.get("router_reason") or lang),
        )
        meta["method"] = "ai_assistant.review"
        meta["issue_count"] = len(issues)
    elif cmd == "analyze":
        module = str(data.get("module") or "assistant")
        session_id = str(data.get("session_id") or "")
        if not session_id:
            return jsonify({"ok": False, "error": "session_id required for analyze"}), 400
        from engines.ai_assistant.analyzer import analyze_session_dir

        analysis = analyze_session_dir(APP_DIR, module, session_id)
        return jsonify({"ok": True, "command": cmd, "analysis": analysis, "meta": meta})

    return jsonify(
        {
            "ok": True,
            "command": cmd,
            "input": text,
            "output": result,
            "changed": result != text,
            "issues": issues,
            "meta": meta,
        }
    )


@bp.post("/api/assistant/text-review")
def api_assistant_text_review():
    """Rule-based translation review (same engine as platform assistant)."""
    blocked = _guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    from engines.ai_assistant.analyzer import analyze_translation_review_segment

    issues = analyze_translation_review_segment(
        source=str(data.get("source") or ""),
        translated=str(data.get("translated") or data.get("text") or ""),
        router_reason=str(data.get("router_reason") or ""),
    )
    return jsonify({"ok": True, "issues": issues, "issue_count": len(issues)})


@bp.get("/api/assistant/trace/<module>/<session_id>")
def api_assistant_analyze_session(module: str, session_id: str):
    blocked = _guard()
    if blocked:
        return blocked
    from engines.ai_assistant.analyzer import analyze_session_dir

    safe_mod = Path(module).name
    safe_sid = Path(session_id).name
    return jsonify({"ok": True, **analyze_session_dir(APP_DIR, safe_mod, safe_sid)})


@bp.get("/api/assistant/commands")
def api_assistant_commands():
    blocked = _guard()
    if blocked:
        return blocked
    return jsonify(
        {
            "ok": True,
            "commands": sorted(set(_COMMANDS.keys())),
            "descriptions": {
                "shorter": "Shorten text to fit slot_ms",
                "conversational": "Naturalize tone (RU)",
                "formal": "Formal polish",
                "polish": "General polish",
                "fix_calque": "Fix common UK→RU calques",
                "review": "Rule-based quality hints",
                "analyze": "Analyze platform trace session",
            },
        }
    )
