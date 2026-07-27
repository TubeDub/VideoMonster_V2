"""Shared request guards for destructive / privileged local APIs."""

from __future__ import annotations

from typing import Any

from flask import Request


def is_local_request(req: Request) -> bool:
    addr = (req.remote_addr or "").strip().lower()
    return addr in ("127.0.0.1", "::1", "localhost")


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def destructive_confirm_error(
    req: Request, data: dict | None = None
) -> tuple[dict, int] | None:
    """Return (payload, http_status) when the destructive gate fails, else None.

    Requires:
    - loopback remote_addr (blocks LAN even if VM_BIND_HOST=0.0.0.0)
    - confirm=true in JSON body or query string
    - X-VM-Destructive-Confirm header (mitigates cross-site simple POSTs)
    """
    if not is_local_request(req):
        return (
            {
                "ok": False,
                "error": "localhost_only",
                "message": "Деструктивные операции хранения разрешены только с localhost",
            },
            403,
        )

    body = data if isinstance(data, dict) else (req.get_json(silent=True) or {})
    confirm = body.get("confirm") if isinstance(body, dict) else None
    if confirm is None:
        confirm = req.args.get("confirm")
    if not _truthy(confirm):
        return (
            {
                "ok": False,
                "error": "confirmation_required",
                "message": "Подтвердите действие: передайте confirm=true",
            },
            400,
        )

    header = (req.headers.get("X-VM-Destructive-Confirm") or "").strip()
    if not _truthy(header):
        return (
            {
                "ok": False,
                "error": "confirm_header_required",
                "message": "Требуется заголовок X-VM-Destructive-Confirm: 1",
            },
            403,
        )

    return None


def require_destructive_confirm(req: Request, data: dict | None = None):
    """Flask response wrapper around :func:`destructive_confirm_error`."""
    err = destructive_confirm_error(req, data)
    if err is None:
        return None
    from flask import jsonify

    payload, code = err
    return jsonify(payload), code
