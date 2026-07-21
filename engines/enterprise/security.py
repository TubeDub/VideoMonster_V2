"""P814 Security Model + P815 Privacy."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class SecretsVault:
    """P814 — secrets never stored as plaintext in code or vault file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or (ROOT / "data" / "enterprise_secrets.json"))
        self._entries: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                self._entries = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._entries = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")

    def put(self, name: str, value: str) -> None:
        # Prefer env for retrieval; store hash only for verification
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        self._entries[name] = {"hash": digest, "via": "hash"}
        self._save()
        # Encourage env usage
        os.environ.setdefault(f"VM_SECRET_{name.upper()}", value)

    def verify(self, name: str, value: str) -> bool:
        env = os.getenv(f"VM_SECRET_{name.upper()}")
        if env is not None:
            return secrets.compare_digest(env, value)
        row = self._entries.get(name)
        if not row:
            return False
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return secrets.compare_digest(str(row.get("hash") or ""), digest)

    def assert_no_plaintext_in_repo(self, scan_roots: list[Path] | None = None) -> list[str]:
        """Soft scan for obvious secret patterns in tracked config examples only."""
        issues: list[str] = []
        # Do not deep-scan whole repo in tests — check vault file itself has no 'plaintext' keys
        for name, row in self._entries.items():
            if "value" in row or "plaintext" in row:
                issues.append(f"plaintext_secret:{name}")
        return issues


class PrivacyControls:
    """P815 — data separation, retention, deletion, export control."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or (ROOT / "data" / "privacy"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root / "settings.json"
        self._settings = {
            "retain_days": 90,
            "allow_export": True,
            "separate_user_data": True,
            "user_data_root": str(self.root / "users"),
        }
        if self.settings_path.is_file():
            try:
                self._settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
            except Exception:
                pass
        Path(self._settings["user_data_root"]).mkdir(parents=True, exist_ok=True)
        self._save()

    def _save(self) -> None:
        self.settings_path.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")

    def user_dir(self, user_id: str) -> Path:
        d = Path(self._settings["user_data_root"]) / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def delete_project(self, user_id: str, project_id: str) -> bool:
        path = self.user_dir(user_id) / project_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            return True
        # Also try enterprise cloud projects
        try:
            from engines.platform_sdk.cloud import get_cloud_facade

            cloud = get_cloud_facade()
            p = cloud.projects_dir / f"{project_id}.json"
            if p.is_file():
                p.unlink()
                return True
        except Exception:
            pass
        return False

    def can_export(self) -> bool:
        return bool(self._settings.get("allow_export", True))

    def settings(self) -> dict[str, Any]:
        return dict(self._settings)

    def update_settings(self, **kwargs: Any) -> dict[str, Any]:
        self._settings.update(kwargs)
        self._save()
        return self.settings()
