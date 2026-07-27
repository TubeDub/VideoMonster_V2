"""Production OAuth scaffolding for Google Drive / OneDrive / Dropbox.

Credentials come from environment variables. When secrets are missing the
remote path is hard-gated (no fake "OAuth connected"). Local filesystem
mirrors remain available as an offline fallback.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Provider → OAuth endpoints + env keys
OAUTH_SPECS: dict[str, dict[str, Any]] = {
    "google_drive": {
        "label": "Google Drive",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        "client_id_env": "VM_GOOGLE_CLIENT_ID",
        "client_secret_env": "VM_GOOGLE_CLIENT_SECRET",
        "redirect_env": "VM_GOOGLE_REDIRECT_URI",
        "default_redirect": "http://127.0.0.1:5000/api/cloud/oauth/google_drive/callback",
        "extra_auth": {"access_type": "offline", "prompt": "consent"},
    },
    "onedrive": {
        "label": "OneDrive",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ["Files.ReadWrite", "offline_access"],
        "client_id_env": "VM_ONEDRIVE_CLIENT_ID",
        "client_secret_env": "VM_ONEDRIVE_CLIENT_SECRET",
        "redirect_env": "VM_ONEDRIVE_REDIRECT_URI",
        "default_redirect": "http://127.0.0.1:5000/api/cloud/oauth/onedrive/callback",
        "extra_auth": {"response_mode": "query"},
    },
    "dropbox": {
        "label": "Dropbox",
        "auth_url": "https://www.dropbox.com/oauth2/authorize",
        "token_url": "https://api.dropboxapi.com/oauth2/token",
        "scopes": [],  # Dropbox app permissions are configured in console
        "client_id_env": "VM_DROPBOX_APP_KEY",
        "client_secret_env": "VM_DROPBOX_APP_SECRET",
        "redirect_env": "VM_DROPBOX_REDIRECT_URI",
        "default_redirect": "http://127.0.0.1:5000/api/cloud/oauth/dropbox/callback",
        "extra_auth": {"token_access_type": "offline"},
    },
}


@dataclass
class OAuthCredentialStatus:
    provider_id: str
    label: str
    configured: bool
    missing: list[str] = field(default_factory=list)
    client_id_set: bool = False
    redirect_uri: str = ""
    has_token: bool = False
    token_expires_at: int | None = None
    oauth_status: str = "not_configured"  # not_configured | needs_auth | connected | expired

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def tokens_path(app_dir: Path) -> Path:
    d = Path(app_dir) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "cloud_oauth_tokens.json"


def load_token_store(app_dir: Path) -> dict[str, Any]:
    path = tokens_path(app_dir)
    if not path.is_file():
        return {"tokens": {}, "pending_states": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"tokens": {}, "pending_states": {}}
        data.setdefault("tokens", {})
        data.setdefault("pending_states", {})
        return data
    except Exception:
        return {"tokens": {}, "pending_states": {}}


def save_token_store(app_dir: Path, data: dict[str, Any]) -> None:
    path = tokens_path(app_dir)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def credential_status(provider_id: str, *, app_dir: Path | None = None) -> OAuthCredentialStatus:
    spec = OAUTH_SPECS.get(provider_id)
    if not spec:
        return OAuthCredentialStatus(
            provider_id=provider_id,
            label=provider_id,
            configured=False,
            missing=["unknown_provider"],
            oauth_status="not_configured",
        )

    client_id = _env(spec["client_id_env"])
    client_secret = _env(spec["client_secret_env"])
    redirect = _env(spec["redirect_env"]) or str(spec["default_redirect"])
    missing: list[str] = []
    if not client_id:
        missing.append(spec["client_id_env"])
    if not client_secret:
        missing.append(spec["client_secret_env"])

    has_token = False
    expires_at: int | None = None
    if app_dir is not None:
        tok = load_token_store(app_dir).get("tokens", {}).get(provider_id) or {}
        if tok.get("access_token") or tok.get("refresh_token"):
            has_token = True
            expires_at = tok.get("expires_at")
            if expires_at and int(expires_at) < int(time.time()) and not tok.get("refresh_token"):
                has_token = False

    if missing:
        status = "not_configured"
    elif has_token:
        if expires_at and int(expires_at) < int(time.time()) and not (
            app_dir and (load_token_store(app_dir).get("tokens", {}).get(provider_id) or {}).get("refresh_token")
        ):
            status = "expired"
        else:
            status = "connected"
    else:
        status = "needs_auth"

    return OAuthCredentialStatus(
        provider_id=provider_id,
        label=str(spec["label"]),
        configured=not missing,
        missing=missing,
        client_id_set=bool(client_id),
        redirect_uri=redirect,
        has_token=has_token,
        token_expires_at=expires_at,
        oauth_status=status,
    )


def require_oauth_credentials(provider_id: str) -> OAuthCredentialStatus:
    """Hard-gate: raise PermissionError when OAuth secrets are missing."""
    st = credential_status(provider_id)
    if not st.configured:
        missing = ", ".join(st.missing)
        raise PermissionError(
            f"{st.label} remote OAuth hard-gated: set env {missing}. "
            "Local mirror remains available as offline fallback. "
            f"/ OAuth не настроен для {st.label}: задайте {missing}. "
            "Локальное зеркало доступно офлайн."
        )
    return st


def build_authorize_url(provider_id: str, *, app_dir: Path, state: str | None = None) -> dict[str, Any]:
    """Return authorize URL or structured hard-gate error (never fake-connected)."""
    st = credential_status(provider_id, app_dir=app_dir)
    if not st.configured:
        missing = ", ".join(st.missing)
        return {
            "ok": False,
            "error": "oauth_credentials_missing",
            "message": f"{st.label}: remote OAuth not configured. Missing: {missing}",
            "message_ru": (
                f"{st.label}: удалённый OAuth не настроен. Нет переменных: {missing}. "
                "Локальное зеркало работает офлайн — удалённое подключение не имитируется."
            ),
            "missing": st.missing,
            "oauth_status": st.oauth_status,
            "local_mirror_available": True,
            "oauth_connected": False,
        }

    spec = OAUTH_SPECS[provider_id]
    state_val = state or secrets.token_urlsafe(24)
    store = load_token_store(app_dir)
    store.setdefault("pending_states", {})[state_val] = {
        "provider_id": provider_id,
        "created_ms": int(time.time() * 1000),
    }
    save_token_store(app_dir, store)

    params: dict[str, str] = {
        "client_id": _env(spec["client_id_env"]),
        "redirect_uri": st.redirect_uri,
        "response_type": "code",
        "state": state_val,
    }
    scopes = list(spec.get("scopes") or [])
    if scopes:
        params["scope"] = " ".join(scopes)
    for k, v in (spec.get("extra_auth") or {}).items():
        params[str(k)] = str(v)

    url = spec["auth_url"] + "?" + urllib.parse.urlencode(params)
    return {
        "ok": True,
        "url": url,
        "state": state_val,
        "provider_id": provider_id,
        "oauth_status": st.oauth_status,
        "redirect_uri": st.redirect_uri,
    }


def _http_form(url: str, data: dict[str, str], *, timeout: float = 30.0) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"OAuth token exchange HTTP {e.code}: {err_body}") from e
    except Exception as e:
        raise RuntimeError(f"OAuth token exchange failed: {e}") from e


def exchange_code(provider_id: str, code: str, *, app_dir: Path, state: str = "") -> dict[str, Any]:
    """Exchange authorization code for tokens; persist under data/cloud_oauth_tokens.json."""
    st = require_oauth_credentials(provider_id)
    spec = OAUTH_SPECS[provider_id]

    store = load_token_store(app_dir)
    if state:
        pending = (store.get("pending_states") or {}).get(state)
        if not pending or pending.get("provider_id") != provider_id:
            return {"ok": False, "error": "invalid_or_expired_state"}

    payload = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": _env(spec["client_id_env"]),
        "client_secret": _env(spec["client_secret_env"]),
        "redirect_uri": st.redirect_uri,
    }
    token = _http_form(str(spec["token_url"]), payload)
    if not token.get("access_token") and not token.get("refresh_token"):
        return {"ok": False, "error": "no_token_in_response", "raw_keys": list(token.keys())}

    expires_in = int(token.get("expires_in") or 0)
    record = {
        "access_token": token.get("access_token") or "",
        "refresh_token": token.get("refresh_token") or "",
        "token_type": token.get("token_type") or "Bearer",
        "scope": token.get("scope") or "",
        "expires_at": int(time.time()) + expires_in if expires_in else None,
        "obtained_ms": int(time.time() * 1000),
        "provider_id": provider_id,
    }
    store.setdefault("tokens", {})[provider_id] = record
    if state and state in (store.get("pending_states") or {}):
        del store["pending_states"][state]
    save_token_store(app_dir, store)
    return {"ok": True, "provider_id": provider_id, "oauth_status": "connected", "expires_at": record["expires_at"]}


def disconnect(provider_id: str, *, app_dir: Path) -> dict[str, Any]:
    store = load_token_store(app_dir)
    removed = bool((store.get("tokens") or {}).pop(provider_id, None))
    save_token_store(app_dir, store)
    return {
        "ok": True,
        "removed": removed,
        "oauth_status": credential_status(provider_id, app_dir=app_dir).oauth_status,
        "local_mirror_available": True,
    }


def get_access_token(provider_id: str, *, app_dir: Path) -> str | None:
    """Return access_token if present (refresh left for remote sync layer)."""
    tok = load_token_store(app_dir).get("tokens", {}).get(provider_id) or {}
    return (tok.get("access_token") or "").strip() or None


def oauth_meta_for_provider(provider_id: str, *, app_dir: Path) -> dict[str, Any]:
    """Honest OAuth metadata for ProviderStatus.meta (never claims remote connected without token)."""
    st = credential_status(provider_id, app_dir=app_dir)
    missing = st.missing or []
    msg_en = ""
    msg_ru = ""
    if not st.configured:
        miss = ", ".join(missing)
        msg_en = f"OAuth hard-gated (local mirror only): missing {miss}"
        msg_ru = f"OAuth закрыт (только локальное зеркало): нет {miss}"
    elif st.oauth_status == "needs_auth":
        msg_en = "OAuth credentials present — authorize via /api/cloud/oauth/.../authorize"
        msg_ru = "Ключи OAuth есть — авторизуйтесь через /api/cloud/oauth/.../authorize"
    return {
        "oauth_status": st.oauth_status,
        "oauth_configured": st.configured,
        "oauth_missing": missing,
        "oauth_remote_gated": not st.configured,
        "oauth_connected": st.oauth_status == "connected",
        "local_mirror_available": True,
        "redirect_uri": st.redirect_uri if st.configured else "",
        "message": msg_en,
        "message_ru": msg_ru,
    }
