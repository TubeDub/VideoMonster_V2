"""Style Pack registry — base + regional styles by target language."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engines.dub_style_presets import (
    get_dub_style,
    list_dub_styles,
    list_style_pack_meta,
    normalize_style_id,
)


def test_base_styles_always() -> None:
    for lang in ("ru", "ja", "en", "pl", "xx"):
        styles = list_dub_styles(target_lang=lang, local_only=True)
        ids = {s["id"] for s in styles}
        for base in ("modern", "documentary", "author", "cinematic"):
            assert base in ids, f"{base} missing for {lang}: {ids}"


def test_regional_ru_ua() -> None:
    styles = list_dub_styles(target_lang="uk", local_only=True)
    ids = {s["id"] for s in styles}
    assert "vhs_classic" in ids
    assert "tv90" in ids
    assert "anime_narrator" not in ids


def test_regional_jp() -> None:
    styles = list_dub_styles(target_lang="ja", local_only=True)
    ids = {s["id"] for s in styles}
    assert "anime_narrator" in ids
    assert "vhs_classic" not in ids


def test_full_library() -> None:
    styles = list_dub_styles(target_lang="ru", local_only=False)
    ids = {s["id"] for s in styles}
    assert "vhs_classic" in ids
    assert "anime_narrator" in ids
    assert "radio90" in ids


def test_legacy_alias() -> None:
    assert normalize_style_id("nineties") == "vhs_classic"
    assert normalize_style_id("cinema") == "cinematic"


def test_vhs_profile() -> None:
    vhs = get_dub_style("vhs_classic")
    assert vhs.region_pack == "ru_ua"
    assert vhs.allow_atempo is False
    assert vhs.original_volume == 0.40


def main() -> int:
    test_base_styles_always()
    test_regional_ru_ua()
    test_regional_jp()
    test_full_library()
    test_legacy_alias()
    test_vhs_profile()
    meta = list_style_pack_meta("ru", local_only=True)
    assert meta["regional_pack"] == "ru_ua"
    print("Style Packs OK —", len(list_dub_styles(local_only=False)), "styles total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
