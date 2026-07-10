"""Server-side UI locale resolution (Accept-Language / env)."""

from __future__ import annotations

import os
from flask import has_request_context, request


def resolve_server_locale(
    explicit: str | None = None,
    accept_language: str | None = None,
) -> str:
    """Return ru | uk | en — mirrors static/js/i18n.js detectUiLang."""
    if explicit:
        lang = explicit.split("-")[0].lower()
        if lang in ("ru", "uk", "en"):
            return lang
        if explicit.lower().startswith("uk"):
            return "uk"
        if explicit.lower().startswith("en"):
            return "en"
    env_lang = (os.getenv("VM_UI_LANG") or os.getenv("LANG") or "").strip()
    if env_lang:
        base = env_lang.split(".")[0].split("_")[0].split("-")[0].lower()
        if base == "uk":
            return "uk"
        if base == "en":
            return "en"
        if base == "ru":
            return "ru"
    header = accept_language or ""
    if has_request_context() and not header:
        header = request.headers.get("Accept-Language", "")
    if header:
        first = header.split(",")[0].strip().lower()
        if first.startswith("uk"):
            return "uk"
        if first.startswith("en"):
            return "en"
        if first.startswith("ru"):
            return "ru"
    return "en"


def locale_from_request(data: dict | None = None) -> str:
    data = data or {}
    explicit = data.get("ui_lang") or data.get("lang")
    if not explicit and has_request_context():
        explicit = request.args.get("lang") or request.args.get("ui_lang")
    accept = None
    if has_request_context():
        accept = request.headers.get("Accept-Language")
    return resolve_server_locale(explicit, accept)
