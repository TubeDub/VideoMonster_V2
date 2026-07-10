#!/usr/bin/env python3
"""Smoke tests for Cloud Platform module."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def main() -> None:
    print("Cloud Platform tests")

    from engines.cloud.config import cloud_platform_enabled
    from engines.cloud.models import StorageMode
    from engines.cloud.providers import PROVIDER_REGISTRY
    from engines.cloud.providers.local import LocalProvider
    from engines.feature_flags.manager import get_feature_manager

    fm = get_feature_manager(ROOT)
    rec = fm.get("cloud_platform")
    assert rec is not None
    assert rec.status == "DEVELOPMENT"
    ok("feature flag registered")

    assert not cloud_platform_enabled()
    ok("disabled by default")

    from engines.cloud.store import CloudStore

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "data").mkdir()
        (base / "output").mkdir()
        (base / "projects" / "cloud").mkdir(parents=True)

        store = CloudStore(base)
        settings = store.load_settings()
        assert "providers" in settings
        ok("default settings")

        sample = base / "output" / "test_dub.mp4"
        sample.write_bytes(b"fake-video-content-for-test")

        os.environ["VM_CLOUD_PLATFORM_ENABLED"] = "1"
        try:
            from engines.cloud.service import CloudPlatformService

            svc = CloudPlatformService(base)
            assert svc.status()["enabled"] is True
            ok("service starts when env enabled")

            result = svc.post_dub_action("test_dub.mp4", "keep_local", title="Test Dub")
            assert result["ok"] is True
            pid = result["project"]["project_id"]
            ok("post-dub keep_local")

            result2 = svc.post_dub_action("test_dub.mp4", "both", provider_id="local")
            assert result2.get("task_id")
            ok("post-dub both enqueues sync")

            projects = svc.list_projects()
            assert len(projects) >= 1
            ok(f"projects registered ({len(projects)})")

            prov = LocalProvider(base, {})
            st = prov.connect()
            assert st.connected
            entry = prov.upload_file(sample, "uploads/test_dub.mp4")
            assert entry.sha256
            ok("local provider upload + checksum")

            files = prov.list_files("uploads")
            assert any(f.path.endswith("test_dub.mp4") for f in files)
            ok("local provider list")

        finally:
            os.environ.pop("VM_CLOUD_PLATFORM_ENABLED", None)

    assert len(PROVIDER_REGISTRY) >= 6
    ok(f"provider adapters ({len(PROVIDER_REGISTRY)})")

    modes = [m.value for m in StorageMode]
    assert "auto_sync" in modes
    ok("storage modes defined")

    print("\nAll cloud platform tests passed.")


if __name__ == "__main__":
    main()
