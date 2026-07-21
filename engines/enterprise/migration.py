"""P805–P806 Migration Engine — open old projects automatically."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from engines.enterprise.types import ENTERPRISE_VERSION

Migrator = Callable[[dict[str, Any]], dict[str, Any]]

_MIGRATIONS: dict[int, Migrator] = {}


def register_migration(version: int, fn: Migrator) -> None:
    _MIGRATIONS[version] = fn


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out.setdefault("schema_version", 1)
    out.setdefault("pipeline_version_bundle", {})
    if "segments_data" in out and "segments" not in out:
        out["segments"] = out.get("segments_data")
    return out


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out["schema_version"] = 2
    out.setdefault("enterprise_version", ENTERPRISE_VERSION)
    # Ensure version bundle present
    if not out.get("pipeline_version_bundle"):
        from engines.enterprise.pipeline_versions import collect_pipeline_versions

        out["pipeline_version_bundle"] = collect_pipeline_versions().to_dict()
    return out


register_migration(1, _migrate_v0_to_v1)
register_migration(2, _migrate_v1_to_v2)

CURRENT_SCHEMA = 2


class MigrationEngine:
    """Unified migration for contracts, versions, structures, profiles, settings."""

    def __init__(self, target_version: int = CURRENT_SCHEMA) -> None:
        self.target_version = target_version

    def migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        current = int(data.get("schema_version") or 0)
        out = dict(data)
        for ver in range(current + 1, self.target_version + 1):
            fn = _MIGRATIONS.get(ver)
            if fn:
                out = fn(out)
                out["schema_version"] = ver
        # Bridge existing migrators
        try:
            from engines.storage.migration import migrate_project_data
            from pathlib import Path
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                out = migrate_project_data(out, Path(tmp))
        except TypeError:
            pass
        except Exception:
            pass
        try:
            from engines.production_hardening.backcompat import migrate_project_info

            out = migrate_project_info(out)
        except Exception:
            pass
        return out

    def migrate_file(self, path: Path | str, *, write: bool = True) -> dict[str, Any]:
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        migrated = self.migrate(data)
        if write:
            # backup
            bak = p.with_suffix(p.suffix + ".bak")
            if not bak.is_file():
                bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            p.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
        return migrated

    def needs_migration(self, data: dict[str, Any]) -> bool:
        return int(data.get("schema_version") or 0) < self.target_version


def open_project_compatible(data: dict[str, Any]) -> dict[str, Any]:
    """P805 — old versions open automatically via Migration Engine."""
    return MigrationEngine().migrate(data)
