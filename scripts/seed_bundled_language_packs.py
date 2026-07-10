"""Download bundled language packs for installer packaging (run once before build)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUNDLED_PAIRS = [
    ("en", "ru"),
    ("ru", "en"),
    ("en", "uk"),
    ("uk", "en"),
    ("ru", "uk"),
    ("uk", "ru"),
]


def main() -> int:
    from engines.model_manager import configure, ensure_profile
    from engines.model_manager.bundled import register_bundled_components
    from engines.model_manager.runtime import prepare_download_session, set_downloads_permitted

    configure(ROOT)
    set_downloads_permitted(False)

    print("Downloading bundled language packs…")
    with prepare_download_session():
        for src, tgt in BUNDLED_PAIRS:
            print(f"  {src} -> {tgt} …")
            r = ensure_profile(ROOT, src, tgt, feature="translate", job_id="seed")
            if not r.ready:
                print(f"    FAILED: {r.error}")
                return 1
        r = ensure_profile(ROOT, "en", "ru", whisper_size="tiny", feature="stt", job_id="seed-whisper")
        if not r.ready:
            print(f"    whisper FAILED: {r.error}")
            return 1

    reg = register_bundled_components(ROOT)
    print(f"Done. Registered: {len(reg.get('registered') or [])} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
