from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = ROOT / "static" / "i18n"


def _load(lang: str) -> dict:
    return json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def test_studio_i18n_keys_present_in_all_locales():
    required = {
        "studio.track.original",
        "studio.track.dub",
        "studio.track.music",
        "studio.track.sfx",
        "studio.track.markers",
        "studio.plugin.loudness",
        "studio.plugin.compressor",
        "studio.btn_split",
        "studio.btn_copy",
        "studio.btn_delete",
        "studio.btn_merge",
    }
    for lang in ("ru", "uk", "en"):
        data = _load(lang)
        missing = sorted(required - set(data.keys()))
        assert not missing, f"{lang} missing keys: {missing}"
