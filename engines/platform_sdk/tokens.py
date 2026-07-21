"""P718 API Tokens — never store secrets in source code."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class TokenStore:
    """
    Tokens live in data/ or env — never in code.
    Env override: VM_API_TOKEN_<NAME>
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or (ROOT / "data" / "api_tokens.json"))
        self._tokens: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                self._tokens = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._tokens = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Store hashes only
        self.path.write_text(json.dumps(self._tokens, indent=2), encoding="utf-8")

    def issue(self, name: str, *, scopes: list[str] | None = None) -> str:
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self._tokens[name] = {
            "hash": digest,
            "scopes": list(scopes or ["read"]),
            "created_at": time.time(),
        }
        self._save()
        return raw  # returned once to caller; not persisted in plaintext

    def verify(self, name: str, raw_token: str) -> bool:
        env_key = f"VM_API_TOKEN_{name.upper()}"
        env_val = os.getenv(env_key)
        if env_val and secrets.compare_digest(env_val, raw_token):
            return True
        row = self._tokens.get(name)
        if not row:
            return False
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        return secrets.compare_digest(str(row.get("hash") or ""), digest)

    def revoke(self, name: str) -> None:
        self._tokens.pop(name, None)
        self._save()

    def list_names(self) -> list[str]:
        return list(self._tokens.keys())


_STORE: TokenStore | None = None


def get_token_store(**kwargs: Any) -> TokenStore:
    global _STORE
    if _STORE is None or kwargs:
        _STORE = TokenStore(**kwargs)
    return _STORE
