"""
VideoMonster V2 — клиент онлайн-сервера лицензий.
Работает поверх офлайн-HMAC; при недоступности сервера — fallback.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = APP_DIR / "data" / "license_server.json"
REQUEST_TIMEOUT = 12


def _load_config() -> dict:
    cfg: dict[str, Any] = {"enabled": False, "url": ""}
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning("license_server config read failed: %s", e)

    env_url = os.getenv("VM_LICENSE_SERVER_URL", "").strip()
    if env_url:
        cfg["url"] = env_url.rstrip("/")
        cfg["enabled"] = True

    if os.getenv("VM_LICENSE_SERVER_ENABLED", "").lower() in ("1", "true", "yes"):
        cfg["enabled"] = True

    return cfg


def is_configured() -> bool:
    cfg = _load_config()
    return bool(cfg.get("enabled") and cfg.get("url"))


def _post(path: str, payload: dict) -> tuple[bool, dict, str]:
    cfg = _load_config()
    base = (cfg.get("url") or "").rstrip("/")
    if not cfg.get("enabled") or not base:
        return False, {}, "server_not_configured"

    url = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "VideoMonster-V2-LicenseClient/1.0",
    }
    token = os.getenv("VM_LICENSE_SERVER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return True, data, ""
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            data = json.loads(err_body)
            msg = data.get("error") or data.get("message") or str(e)
        except Exception:
            msg = str(e)
        logger.warning("License server HTTP %s: %s", e.code, msg)
        return False, {}, msg
    except Exception as e:
        logger.warning("License server request failed: %s", e)
        return False, {}, str(e)


def online_activate(key: str, device_id: str) -> tuple[bool, dict, str]:
    ok, data, err = _post(
        "/v1/activate",
        {"key": key, "device_id": device_id},
    )
    if not ok:
        return False, {}, err
    if not data.get("ok"):
        return False, {}, data.get("error") or "activation_failed"
    return True, data.get("license") or data, data.get("message", "")


def online_sync(key: str, device_id: str) -> tuple[bool, dict, str]:
    if not key:
        return False, {}, "no_key"
    ok, data, err = _post(
        "/v1/sync",
        {"key": key, "device_id": device_id},
    )
    if not ok:
        return False, {}, err
    if data.get("revoked"):
        return True, {"revoked": True, "message": data.get("message", "")}, ""
    return True, data.get("license") or data, ""


def online_heartbeat(device_id: str, tier: str) -> tuple[bool, dict, str]:
    ok, data, err = _post(
        "/v1/heartbeat",
        {"device_id": device_id, "tier": tier},
    )
    if not ok:
        return False, {}, err
    return True, data, ""
