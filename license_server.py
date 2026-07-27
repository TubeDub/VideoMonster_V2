#!/usr/bin/env python3
"""
VideoMonster V2 — онлайн-сервер лицензий (для владельца).

Запуск:
  set VM_LICENSE_SECRET=your-secret
  set VM_OWNER_TOKEN=your-owner-token
  python license_server.py

Клиенты указывают URL в data/license_server.json:
  { "enabled": true, "url": "http://YOUR-IP:8787" }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, request

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from engines.license_manager import (  # noqa: E402
    KEY_TYPES,
    TIER_LABELS,
    generate_key,
    validate_key_format,
)

DB_FILE = APP_DIR / "data" / "license_server_db.json"
app = Flask(__name__)


def _owner_ok() -> bool:
    # Refuse the public default when binding a network-facing license server.
    token = os.getenv("VM_OWNER_TOKEN", "").strip()
    if not token or token == "vm-owner-local":
        return False
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == token
    return request.headers.get("X-VM-Owner-Token", "") == token


def _load_db() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            print(f"[license_server] warning: could not load {DB_FILE}: {e}", file=sys.stderr)
    return {"activations": {}, "revoked_keys": []}


def _save_db(db: dict) -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_revoked(db: dict, key: str) -> bool:
    return key.upper() in [k.upper() for k in db.get("revoked_keys", [])]


def _license_payload(rec: dict) -> dict:
    return {
        "tier": rec.get("tier"),
        "key": rec.get("key"),
        "key_type": rec.get("key_type"),
        "expires_at": rec.get("expires_at"),
        "revoked": rec.get("revoked", False),
        "device_id": rec.get("device_id"),
        "message": rec.get("message", ""),
    }


@app.get("/")
def health():
    return jsonify({"service": "VideoMonster License Server", "ok": True, "version": 1})


@app.post("/v1/activate")
def v1_activate():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    device_id = (body.get("device_id") or "").strip()

    if not key or not device_id:
        return jsonify({"error": "key and device_id required"}), 400

    ok, prefix, err = validate_key_format(key)
    if not ok:
        return jsonify({"error": err}), 400

    db = _load_db()
    if _is_revoked(db, key):
        return jsonify({"error": "Ключ отключён владельцем"}), 403

    tier, days, key_type = KEY_TYPES[prefix]
    now = time.time()
    expires = None if days is None else now + days * 86400

    activations = db.setdefault("activations", {})
    existing = activations.get(key)

    if existing and existing.get("device_id") not in (None, "", device_id):
        if not existing.get("allow_rebind"):
            return jsonify(
                {
                    "error": "Ключ уже активирован на другом устройстве. "
                    "Обратитесь к владельцу для переноса.",
                }
            ), 409

    rec = {
        "key": key,
        "tier": tier,
        "key_type": key_type,
        "device_id": device_id,
        "activated_at": now,
        "expires_at": expires,
        "revoked": False,
        "message": f"Активировано: {TIER_LABELS.get(tier, tier)} ({key_type})",
        "last_seen": now,
    }
    activations[key] = rec
    _save_db(db)

    return jsonify({"ok": True, "message": rec["message"], "license": _license_payload(rec)})


@app.post("/v1/sync")
def v1_sync():
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    device_id = (body.get("device_id") or "").strip()

    if not key:
        return jsonify({"ok": True, "license": None})

    db = _load_db()
    if _is_revoked(db, key):
        return jsonify(
            {
                "ok": True,
                "revoked": True,
                "message": "Ключ отключён владельцем. Обратитесь к нему за новым ключом.",
            }
        )

    rec = db.get("activations", {}).get(key)
    if not rec:
        return jsonify({"ok": True, "license": None})

    if rec.get("device_id") and device_id and rec["device_id"] != device_id:
        return jsonify({"error": "Ключ привязан к другому устройству"}), 403

    rec["last_seen"] = time.time()
    if rec.get("revoked"):
        return jsonify({"ok": True, "revoked": True, "message": "Ключ отключён"})

    _save_db(db)
    return jsonify({"ok": True, "license": _license_payload(rec)})


@app.post("/v1/heartbeat")
def v1_heartbeat():
    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    db = _load_db()
    for rec in db.get("activations", {}).values():
        if rec.get("device_id") == device_id:
            rec["last_seen"] = time.time()
    _save_db(db)
    return jsonify({"ok": True})


@app.post("/v1/admin/revoke")
def v1_admin_revoke():
    if not _owner_ok():
        return jsonify({"error": "owner token required"}), 403
    key = ((request.get_json(silent=True) or {}).get("key") or "").strip().upper()
    if not key:
        return jsonify({"error": "key required"}), 400

    db = _load_db()
    revoked = db.setdefault("revoked_keys", [])
    if key not in revoked:
        revoked.append(key)
    if key in db.get("activations", {}):
        db["activations"][key]["revoked"] = True
    _save_db(db)
    return jsonify({"ok": True, "message": f"Key {key} revoked"})


@app.post("/v1/admin/extend")
def v1_admin_extend():
    if not _owner_ok():
        return jsonify({"error": "owner token required"}), 403
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    days = body.get("days")
    lifetime = body.get("lifetime") or body.get("mode") == "lifetime"

    db = _load_db()
    rec = db.get("activations", {}).get(key)
    if not rec:
        return jsonify({"error": "activation not found"}), 404

    now = time.time()
    if lifetime:
        rec["tier"] = "premium"
        rec["expires_at"] = None
        rec["key_type"] = "LIFETIME"
    else:
        add = int(days or 7)
        base = max(now, float(rec.get("expires_at") or now))
        rec["expires_at"] = base + add * 86400
    rec["revoked"] = False
    if key in db.get("revoked_keys", []):
        db["revoked_keys"] = [k for k in db["revoked_keys"] if k.upper() != key]
    _save_db(db)
    return jsonify({"ok": True, "license": _license_payload(rec)})


@app.post("/v1/admin/generate")
def v1_admin_generate():
    if not _owner_ok():
        return jsonify({"error": "owner token required"}), 403
    key_type = (request.get_json(silent=True) or {}).get("type") or "TEST-7"
    try:
        key = generate_key(key_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "key": key, "type": key_type})


@app.post("/v1/admin/rebind")
def v1_admin_rebind():
    """Разрешить перенос ключа на новое устройство."""
    if not _owner_ok():
        return jsonify({"error": "owner token required"}), 403
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().upper()
    db = _load_db()
    rec = db.get("activations", {}).get(key)
    if not rec:
        return jsonify({"error": "not found"}), 404
    rec["device_id"] = None
    rec["allow_rebind"] = True
    _save_db(db)
    return jsonify({"ok": True, "message": "Rebind allowed"})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    args = p.parse_args()
    print(f"VideoMonster License Server on http://{args.host}:{args.port}")
    if not os.getenv("VM_OWNER_TOKEN", "").strip():
        print("WARNING: set VM_OWNER_TOKEN — default/empty token is rejected")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
