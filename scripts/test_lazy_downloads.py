"""Lazy download gate tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_downloads_blocked_by_default():
    from engines.model_manager.runtime import (
        ModelNotPreparedError,
        assert_downloads_allowed,
        downloads_permitted,
        prepare_download_session,
        set_downloads_permitted,
    )

    set_downloads_permitted(False)
    assert not downloads_permitted()
    try:
        assert_downloads_allowed("test")
        assert False, "should raise"
    except ModelNotPreparedError:
        pass

    with prepare_download_session():
        assert downloads_permitted()
        assert_downloads_allowed("test")

    assert not downloads_permitted()


def test_translate_profile_skips_whisper():
    from engines.model_manager.profiles import profile_for_pair

    app = ROOT
    dub = profile_for_pair(app, "en", "uk", feature="dub")
    tr = profile_for_pair(app, "en", "uk", feature="translate")
    stt = profile_for_pair(app, "en", "uk", feature="stt")
    assert any(i.component_id == "whisper" for i in dub)
    assert not any(i.component_id == "whisper" for i in tr)
    assert any(i.component_id == "whisper" for i in stt)
    assert not any(i.component_id == "tts" for i in tr)


def test_argos_supports_without_download():
    from engines.mt.argos_engine import ArgosEngine

    eng = ArgosEngine()
    if not eng.is_available():
        return
    # Must not raise or download — index-only check
    eng.supports_pair("en", "uk")


def main() -> int:
    test_downloads_blocked_by_default()
    test_translate_profile_skips_whisper()
    test_argos_supports_without_download()
    print("lazy download tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
