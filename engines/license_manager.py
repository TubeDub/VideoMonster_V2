"""
VideoMonster V2 — License Manager
Demo / Basic / Premium tiers with offline validation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
LICENSE_FILE = APP_DIR / "license.json"
REVOKED_FILE = APP_DIR / "data" / "license_revoked.json"
REGISTRY_FILE = APP_DIR / "data" / "license_registry.json"

SYNC_GRACE_DAYS = 14
BASIC_TRANSLATE_DAILY_LIMIT = 15

KEY_PATTERN = re.compile(r"^VM-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})$")

# Prefix in key -> (tier, days or None for lifetime)
KEY_TYPES: dict[str, tuple[str, int | None, str]] = {
    "T7XX": ("demo", 7, "TEST-7"),
    "T30X": ("demo", 30, "TEST-30"),
    "PRWK": ("premium", 7, "PREMIUM-WEEK"),
    "PRMO": ("premium", 30, "PREMIUM-MONTH"),
    "PRYR": ("premium", 365, "PREMIUM-YEAR"),
    "LIFE": ("premium", None, "LIFETIME"),
}

TIER_LABELS = {
    "demo": "Demo (тест)",
    "basic": "Basic",
    "premium": "Premium",
}

FEATURES = {
    "auto_dub": ("demo", "basic", "premium"),
    "mp4_export": ("demo", "premium"),
    "tts_unlimited": ("demo", "premium"),
    "translate_unlimited": ("demo", "premium"),
    "batch_processing": ("demo", "premium"),
    "fast_whisper_models": ("demo", "premium"),
    "view_ui": ("demo", "basic", "premium"),
    "open_projects": ("demo", "basic", "premium"),
    "export_srt": ("demo", "basic", "premium"),
    "translate_limited": ("demo", "basic", "premium"),
    "reader": ("demo", "basic", "premium"),
    "manual_dub": ("demo", "basic", "premium"),
}


def _license_secret() -> bytes:
    env = os.getenv("VM_LICENSE_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    secret_file = APP_DIR / "data" / "license_secret.txt"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip().encode("utf-8")
    return b"VideoMonster-V2-Owner-Change-This-Secret"


def _device_id() -> str:
    node = uuid.getnode()
    host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "vm"
    raw = f"{node}-{host}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _checksum(prefix: str, body: str) -> str:
    msg = f"{prefix}-{body}".encode()
    digest = hmac.new(_license_secret(), msg, hashlib.sha256).hexdigest()
    return digest[:4].upper()


def generate_key(key_type: str) -> str:
    """Owner tool: generate VM-XXXX-XXXX-XXXX key."""
    prefix_map = {
        "TEST-7": "T7XX",
        "TEST-30": "T30X",
        "PREMIUM-WEEK": "PRWK",
        "PREMIUM-MONTH": "PRMO",
        "PREMIUM-YEAR": "PRYR",
        "LIFETIME": "LIFE",
    }
    prefix = prefix_map.get(key_type.upper())
    if not prefix:
        raise ValueError(f"Unknown key type: {key_type}")

    import secrets
    import string

    alphabet = string.ascii_uppercase + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(4))
    sig = _checksum(prefix, body)
    return f"VM-{prefix}-{body}-{sig}"


def validate_key_format(key: str) -> tuple[bool, str, str]:
    key = key.strip().upper()
    m = KEY_PATTERN.match(key)
    if not m:
        return False, "", "Неверный формат ключа. Ожидается VM-XXXX-XXXX-XXXX"
    prefix, body, sig = m.group(1), m.group(2), m.group(3)
    if prefix not in KEY_TYPES:
        return False, "", "Неизвестный тип ключа"
    expected = _checksum(prefix, body)
    if not hmac.compare_digest(sig, expected):
        return False, "", "Ключ недействителен (ошибка проверки)"
    return True, prefix, ""


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_revoked(key: str) -> bool:
    revoked = _load_json(REVOKED_FILE, {"keys": []})
    return key.upper() in [k.upper() for k in revoked.get("keys", [])]


def _register_key(key: str, meta: dict) -> None:
    reg = _load_json(REGISTRY_FILE, {"keys": {}})
    reg.setdefault("keys", {})[key.upper()] = meta
    _save_json(REGISTRY_FILE, reg)


def load_license() -> dict:
    data = _load_json(LICENSE_FILE, {})
    if not data:
        return _create_initial_demo()
    return _normalize_license(data)


def _create_initial_demo() -> dict:
    now = time.time()
    data = {
        "tier": "demo",
        "key": "",
        "key_type": "AUTO-DEMO-7",
        "activated_at": now,
        "expires_at": now + 7 * 86400,
        "device_id": _device_id(),
        "last_sync_at": now,
        "auto_demo": True,
        "usage": {"translate_count": 0, "translate_day": _today()},
    }
    save_license(data)
    logger.info("Created automatic 7-day demo license")
    return data


def save_license(data: dict) -> None:
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _save_json(LICENSE_FILE, data)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_license(data: dict) -> dict:
    data.setdefault("usage", {"translate_count": 0, "translate_day": _today()})
    data.setdefault("device_id", _device_id())
    data.setdefault("last_sync_at", time.time())
    return data


def get_effective_tier(data: dict | None = None) -> str:
    data = data or load_license()
    if data.get("revoked") or _is_revoked(data.get("key", "")):
        return "basic"

    tier = data.get("tier", "basic")
    expires = data.get("expires_at")

    if tier == "premium":
        if expires is None:
            return "premium"
        if expires > time.time():
            return "premium"
        return "basic"

    if tier == "demo":
        if expires and expires > time.time():
            return "demo"
        return "basic"

    return "basic"


def has_feature(feature: str, data: dict | None = None) -> bool:
    tier = get_effective_tier(data)
    allowed = FEATURES.get(feature, ())
    return tier in allowed


def check_translate_allowed(data: dict | None = None) -> tuple[bool, str]:
    data = data or load_license()
    tier = get_effective_tier(data)

    if has_feature("translate_unlimited", data):
        return True, ""

    usage = data.setdefault("usage", {"translate_count": 0, "translate_day": _today()})
    today = _today()
    if usage.get("translate_day") != today:
        usage["translate_count"] = 0
        usage["translate_day"] = today

    count = int(usage.get("translate_count", 0))
    if count >= BASIC_TRANSLATE_DAILY_LIMIT:
        return False, (
            f"Лимит переводов Basic: {BASIC_TRANSLATE_DAILY_LIMIT} в день. "
            "Обратитесь к владельцу для Premium-ключа."
        )

    usage["translate_count"] = count + 1
    save_license(data)
    return True, ""


def _apply_remote_license(data: dict, remote: dict) -> dict:
    """Merge server license fields into local record."""
    if remote.get("revoked"):
        data["revoked"] = True
        data["tier"] = "basic"
        return data

    for field in ("tier", "key", "key_type", "expires_at", "device_id"):
        if field in remote and remote[field] is not None:
            data[field] = remote[field]

    data["revoked"] = False
    data["last_online_sync"] = time.time()
    if remote.get("message"):
        data["server_message"] = remote["message"]
    return data


def activate_key(key: str) -> tuple[bool, dict, str]:
    key = key.strip().upper()
    ok, prefix, err = validate_key_format(key)
    if not ok:
        return False, {}, err

    if _is_revoked(key):
        return False, {}, "Этот ключ отключён владельцем. Обратитесь к нему за новым ключом."

    device = _device_id()
    online_msg = ""

    try:
        from engines.license_server_client import is_configured, online_activate

        if is_configured():
            ok_net, remote, net_err = online_activate(key, device)
            if ok_net and remote:
                data = _normalize_license(
                    {
                        "tier": remote.get("tier", "basic"),
                        "key": key,
                        "key_type": remote.get("key_type", ""),
                        "activated_at": time.time(),
                        "expires_at": remote.get("expires_at"),
                        "device_id": device,
                        "last_sync_at": time.time(),
                        "last_online_sync": time.time(),
                        "auto_demo": False,
                        "revoked": False,
                        "usage": {"translate_count": 0, "translate_day": _today()},
                    }
                )
                save_license(data)
                _register_key(key, {"type": data.get("key_type"), "device": device, "online": True})
                msg = remote.get("message") or f"Активировано онлайн: {TIER_LABELS.get(data['tier'], data['tier'])}"
                return True, data, msg
            if net_err and "другом устройстве" in net_err.lower():
                return False, {}, net_err
            if net_err and net_err not in ("server_not_configured",):
                online_msg = " (сервер недоступен — офлайн-активация)"
                logger.info("Online activation fallback: %s", net_err)
    except Exception as e:
        logger.warning("Online activation error: %s", e)
        online_msg = " (офлайн-режим)"

    tier, days, key_type = KEY_TYPES[prefix]
    now = time.time()
    expires = None if days is None else now + days * 86400

    data = {
        "tier": tier,
        "key": key,
        "key_type": key_type,
        "activated_at": now,
        "expires_at": expires,
        "device_id": device,
        "last_sync_at": now,
        "auto_demo": False,
        "revoked": False,
        "usage": {"translate_count": 0, "translate_day": _today()},
    }
    save_license(data)
    _register_key(key, {"type": key_type, "activated_at": now, "device": device})

    msg = f"Активировано: {TIER_LABELS.get(tier, tier)} ({key_type}){online_msg}"
    return True, data, msg


def extend_license(days: int | None) -> tuple[bool, str]:
    """Owner: extend current license. days=None => lifetime premium."""
    data = load_license()
    now = time.time()

    if days is None:
        data["tier"] = "premium"
        data["expires_at"] = None
        data["key_type"] = "LIFETIME"
        save_license(data)
        return True, "Premium активирован бессрочно"

    base = max(now, float(data.get("expires_at") or now))
    data["expires_at"] = base + days * 86400
    if data.get("tier") not in ("premium", "demo"):
        data["tier"] = "demo"
    save_license(data)
    return True, f"Лицензия продлена на {days} дн."


def deactivate() -> dict:
    data = {
        "tier": "basic",
        "key": "",
        "key_type": "",
        "activated_at": time.time(),
        "expires_at": None,
        "device_id": _device_id(),
        "last_sync_at": time.time(),
        "auto_demo": False,
        "usage": {"translate_count": 0, "translate_day": _today()},
    }
    save_license(data)
    return data


def revoke_key(key: str) -> None:
    revoked = _load_json(REVOKED_FILE, {"keys": []})
    keys = revoked.setdefault("keys", [])
    ku = key.strip().upper()
    if ku not in keys:
        keys.append(ku)
    _save_json(REVOKED_FILE, revoked)


def try_sync() -> dict:
    """Sync with online license server when configured; always updates local timestamp."""
    from engines.translation_compat import has_internet

    data = load_license()
    online = has_internet()
    synced_online = False
    server_message = ""

    if online:
        try:
            from engines.license_server_client import is_configured, online_sync

            if is_configured() and data.get("key"):
                ok, remote, err = online_sync(data["key"], data.get("device_id") or _device_id())
                if ok:
                    if remote.get("revoked"):
                        data["revoked"] = True
                        data["tier"] = "basic"
                        server_message = remote.get("message", "")
                        synced_online = True
                    elif remote:
                        data = _apply_remote_license(data, remote)
                        synced_online = True
                elif err:
                    logger.info("Online sync skipped: %s", err)
        except Exception as e:
            logger.warning("Online sync failed: %s", e)

        data["last_sync_at"] = time.time()
        save_license(data)

    return {
        "online": online,
        "synced": online,
        "synced_online": synced_online,
        "server_message": server_message,
        "server_configured": _is_server_configured(),
        "last_sync_at": data.get("last_sync_at"),
        "last_online_sync": data.get("last_online_sync"),
    }


def _is_server_configured() -> bool:
    try:
        from engines.license_server_client import is_configured

        return is_configured()
    except Exception:
        return False


def _is_local_install() -> bool:
    """Local TubeDub copy — no remote license enforcement UX."""
    if os.getenv("VM_LOCAL", "").strip().lower() in ("1", "true", "yes"):
        return True
    if (APP_DIR / "data" / ".owner_initialized").is_file():
        return True
    return not _is_server_configured()


def get_status() -> dict:
    data = load_license()
    tier = get_effective_tier(data)
    expires = data.get("expires_at")
    now = time.time()

    days_left = None
    if expires:
        days_left = max(0, int((expires - now) / 86400))

    demo_expired = (
        data.get("tier") == "demo"
        and expires
        and expires <= now
        and not data.get("auto_demo", False)
    ) or (
        data.get("tier") == "demo"
        and tier == "basic"
        and data.get("key")
    )

    auto_demo_expired = data.get("auto_demo") and tier == "basic"

    last_sync = float(data.get("last_sync_at") or now)
    sync_overdue = (now - last_sync) > SYNC_GRACE_DAYS * 86400

    local_install = _is_local_install()
    demo_expired_flag = auto_demo_expired or demo_expired

    message = ""
    if tier == "demo" and days_left is not None:
        message = f"Тестовый период: осталось {days_left} дн."
    elif demo_expired_flag:
        if local_install:
            message = ""
        else:
            message = "Демо завершено. Доступен базовый режим."
    elif tier == "basic":
        message = "Basic — базовый режим. Premium открывает MP4 и безлимитные переводы."
    elif tier == "premium":
        if expires is None:
            message = "Premium — полный доступ (бессрочно)"
        elif days_left is not None:
            message = f"Premium — осталось {days_left} дн."

    sync_warning = ""
    if sync_overdue:
        sync_warning = (
            "Давно не было связи с сервером лицензий. "
            "Подключите интернет, когда будет возможность — данные сохранены."
        )
    if data.get("server_message") and data.get("revoked"):
        sync_warning = data["server_message"]

    return {
        "tier": tier,
        "raw_tier": data.get("tier", "basic"),
        "key": data.get("key", ""),
        "key_type": data.get("key_type", ""),
        "label": TIER_LABELS.get(tier, tier),
        "is_premium": tier == "premium",
        "is_demo": tier == "demo",
        "is_basic": tier == "basic",
        "expires_at": expires,
        "days_left": days_left,
        "activated_at": data.get("activated_at"),
        "message": message,
        "sync_warning": sync_warning,
        "demo_expired": demo_expired_flag,
        "is_local_install": local_install,
        "server_configured": _is_server_configured(),
        "last_online_sync": data.get("last_online_sync"),
        "features": {
            f: has_feature(f, data) for f in FEATURES
        },
        "translate_limit": None if has_feature("translate_unlimited", data) else BASIC_TRANSLATE_DAILY_LIMIT,
        "translate_used": data.get("usage", {}).get("translate_count", 0),
        "device_id": data.get("device_id"),
    }


def require_feature(feature: str) -> tuple[bool, str]:
    if has_feature(feature):
        return True, ""
    tier = get_effective_tier()
    if tier == "basic":
        return False, (
            "Эта функция доступна в Premium или в тестовом периоде. "
            "Обратитесь к владельцу VideoMonster за ключом."
        )
    return False, "Функция недоступна с текущей лицензией."
