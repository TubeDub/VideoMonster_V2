"""Sync release channels between Feature Flags and Module Catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.tubedub.release import ReleaseChannel, parse_release_channel


def derive_channel_from_feature(rec: Any) -> str:
    if getattr(rec, "release_channel", ""):
        return parse_release_channel(rec.release_channel).value
    if getattr(rec, "auto_disabled", False) or not rec.enabled:
        return ReleaseChannel.DISABLED.value
    st = (rec.status or "").upper()
    if st in ("READY", "STABLE"):
        return ReleaseChannel.RELEASE.value
    if st in ("DISABLED", "NOT_IMPLEMENTED"):
        return ReleaseChannel.DISABLED.value
    return ReleaseChannel.DEVELOPER.value


def sync_catalog_with_features(app_dir: Path) -> dict[str, str]:
    """Apply Feature Flag release_channel to tubedub_modules catalog entries."""
    from engines.feature_flags.manager import get_feature_manager
    from engines.tubedub.catalog import ModuleCatalog

    fm = get_feature_manager(app_dir)
    catalog = ModuleCatalog(app_dir)
    changes: dict[str, str] = {}
    for entry in catalog.all():
        fid = entry.feature_id or entry.id
        feat = fm.get(fid)
        if not feat:
            continue
        ch = derive_channel_from_feature(feat)
        if entry.release_channel != ch:
            changes[entry.id] = ch
            entry.release_channel = ch
    return changes


def effective_release_channel(app_dir: Path, feature_id: str) -> ReleaseChannel:
    from engines.feature_flags.manager import get_feature_manager

    rec = get_feature_manager(app_dir).get(feature_id)
    if not rec:
        return ReleaseChannel.DISABLED
    return parse_release_channel(derive_channel_from_feature(rec))
