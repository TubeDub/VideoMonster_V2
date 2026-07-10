"""Stress Test module tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_module_imports():
    from engines.stress_test.guards import allow_stress_test
    from engines.stress_test.discovery import list_test_videos, ensure_sample_hint
    from engines.stress_test.report import write_stress_reports

    ensure_sample_hint(ROOT)
    assert isinstance(list_test_videos(ROOT), list)
    assert allow_stress_test(ui_dev=True) is True


def test_api_registered():
    from app import app

    rules = [r.rule for r in app.url_map.iter_rules()]
    assert "/api/stress-test/access" in rules
    assert "/api/stress-test/start" in rules


def test_access_endpoint():
    os.environ["VM_DEV_MODE"] = "1"
    from app import app

    client = app.test_client()
    r = client.get("/api/stress-test/access")
    assert r.status_code == 200
    assert r.get_json().get("allowed") is True


def test_report_empty_batch():
    from engines.stress_test.report import write_stress_reports

    batch = {
        "batch_id": "test",
        "version": "2.0.0",
        "total": 0,
        "passed": 0,
        "failed": 0,
        "results": [],
        "summary": {},
    }
    paths = write_stress_reports(batch, app_dir=ROOT)
    assert Path(paths["txt"]).is_file()
    assert Path(paths["html"]).is_file()


def test_app_without_module_safe():
    """App loads with stress test registered."""
    import app as app_mod

    assert app_mod.app.name == "app"


def main() -> int:
    test_module_imports()
    test_api_registered()
    test_access_endpoint()
    test_report_empty_batch()
    test_app_without_module_safe()
    print("stress test module: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
