"""P801–P802 Enterprise Configuration store (separate from code)."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from engines.enterprise.types import ConfigDomain, ConfigurationRecord

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = ROOT / "data" / "enterprise_config"


DEFAULT_DOMAIN_DATA: dict[str, dict[str, Any]] = {
    ConfigDomain.PIPELINE.value: {"mode": "semantic_v3", "native_te": True},
    ConfigDomain.TRANSLATION.value: {"backend": "heuristic", "min_similarity": 0.55},
    ConfigDomain.DUB.value: {"engine": "dub_engine_v2", "require_wav": False},
    ConfigDomain.SCHEDULER.value: {"owner": "scheduler", "api": "update_audio_time"},
    ConfigDomain.DECISION.value: {"profile": "Movie"},
    ConfigDomain.TTS.value: {"provider": "edge-offline", "platform": "voice_platform"},
    ConfigDomain.DIAGNOSTICS.value: {"studio_qa": True, "openddf": True},
    ConfigDomain.STUDIO.value: {"views": ["pipeline", "timeline", "review", "decision"]},
    ConfigDomain.PLUGINS.value: {"enabled": True, "sandbox": True},
    ConfigDomain.CLOUD.value: {"mode": "local", "backup": True},
}


class EnterpriseConfigStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or DEFAULT_STORE)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = self.root / "index.json"
        self._records: dict[str, ConfigurationRecord] = {}
        self._load()
        if not self._records:
            self._seed_defaults()

    def _load(self) -> None:
        if not self._index.is_file():
            return
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
            for domain, row in (raw.get("domains") or {}).items():
                self._records[domain] = ConfigurationRecord(
                    domain=domain,
                    configuration_uuid=str(row.get("configuration_uuid") or uuid.uuid4()),
                    version=str(row.get("version") or "1.0.0"),
                    created=float(row.get("created") or time.time()),
                    updated=float(row.get("updated") or time.time()),
                    migration_version=int(row.get("migration_version") or 1),
                    compatibility=str(row.get("compatibility") or ">=6.0.0"),
                    rollback_point=str(row.get("rollback_point") or ""),
                    profile=str(row.get("profile") or "default"),
                    data=dict(row.get("data") or {}),
                )
        except Exception:
            self._records = {}

    def _save(self) -> None:
        payload = {
            "domains": {k: v.to_dict() for k, v in self._records.items()},
            "updated": time.time(),
        }
        self._index.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for domain, rec in self._records.items():
            (self.root / f"{domain}.json").write_text(
                json.dumps(rec.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _seed_defaults(self) -> None:
        for domain, data in DEFAULT_DOMAIN_DATA.items():
            self._records[domain] = ConfigurationRecord(domain=domain, data=dict(data))
        self._save()

    def get(self, domain: ConfigDomain | str) -> ConfigurationRecord:
        key = domain.value if isinstance(domain, ConfigDomain) else str(domain)
        if key not in self._records:
            self._records[key] = ConfigurationRecord(domain=key, data={})
            self._save()
        return self._records[key]

    def update(
        self,
        domain: ConfigDomain | str,
        data: dict[str, Any],
        *,
        profile: str | None = None,
        bump_version: bool = True,
    ) -> ConfigurationRecord:
        rec = self.get(domain)
        # Snapshot rollback point
        snap = self.root / "rollbacks" / rec.domain
        snap.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        rb = snap / f"{stamp}.json"
        rb.write_text(json.dumps(rec.to_dict(), indent=2), encoding="utf-8")
        rec.rollback_point = str(rb)
        rec.data.update(data)
        if profile:
            rec.profile = profile
        rec.updated = time.time()
        if bump_version:
            major, minor, patch = _parse_ver(rec.version)
            rec.version = f"{major}.{minor}.{patch + 1}"
        self._save()
        return rec

    def set_profile(self, domain: ConfigDomain | str, profile: str) -> ConfigurationRecord:
        return self.update(domain, {}, profile=profile, bump_version=False)

    def export_all(self, path: Path | str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({k: v.to_dict() for k, v in self._records.items()}, indent=2),
            encoding="utf-8",
        )
        return out

    def import_all(self, path: Path | str, *, merge: bool = True) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for domain, row in data.items():
            if not merge and domain in self._records:
                continue
            self._records[domain] = ConfigurationRecord(
                domain=domain,
                configuration_uuid=str(row.get("configuration_uuid") or uuid.uuid4()),
                version=str(row.get("version") or "1.0.0"),
                created=float(row.get("created") or time.time()),
                updated=time.time(),
                migration_version=int(row.get("migration_version") or 1),
                compatibility=str(row.get("compatibility") or ">=6.0.0"),
                rollback_point=str(row.get("rollback_point") or ""),
                profile=str(row.get("profile") or "default"),
                data=dict(row.get("data") or {}),
            )
        self._save()

    def rollback(self, domain: ConfigDomain | str) -> ConfigurationRecord:
        rec = self.get(domain)
        if not rec.rollback_point or not Path(rec.rollback_point).is_file():
            raise FileNotFoundError("No rollback point")
        prev = json.loads(Path(rec.rollback_point).read_text(encoding="utf-8"))
        restored = ConfigurationRecord(
            domain=rec.domain,
            configuration_uuid=str(prev.get("configuration_uuid") or rec.configuration_uuid),
            version=str(prev.get("version") or rec.version),
            created=float(prev.get("created") or rec.created),
            updated=time.time(),
            migration_version=int(prev.get("migration_version") or rec.migration_version),
            compatibility=str(prev.get("compatibility") or rec.compatibility),
            rollback_point=rec.rollback_point,
            profile=str(prev.get("profile") or "default"),
            data=dict(prev.get("data") or {}),
        )
        self._records[rec.domain] = restored
        self._save()
        return restored

    def list_domains(self) -> list[str]:
        return list(self._records.keys())


def _parse_ver(v: str) -> tuple[int, int, int]:
    parts = str(v).split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int("".join(c for c in p if c.isdigit()) or "0"))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


_STORE: EnterpriseConfigStore | None = None


def get_config_store(**kwargs: Any) -> EnterpriseConfigStore:
    global _STORE
    if _STORE is None or kwargs:
        _STORE = EnterpriseConfigStore(**kwargs)
    return _STORE
