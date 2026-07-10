"""
Style Packs API — base + regional profiles from styles/ directory.
Новый стиль = JSON в styles/base/ или styles/regional/<pack>/.
"""

from __future__ import annotations

from typing import Any

from engines.dub_style_loader import DubStylePreset, get_style_registry, reload_style_registry

DEFAULT_DUB_STYLE = "modern"

LEGACY_STYLE_ALIASES: dict[str, str] = {
    "vhs": "vhs_classic",
    "nineties": "vhs_classic",
    "cinema": "cinematic",
    "atmosphere": "cinematic",
    "full_dub": "modern",
    "classic_voiceover": "documentary",
    "classic": "documentary",
    "replace": "modern",
    "mix": "documentary",
    "language_learning": "documentary",
}

ORIGINAL_VOLUME_PRESETS = (0.0, 0.20, 0.38, 0.40, 0.50, 0.70, 1.0)

STABLE_BASELINE_TAG = "stable-2026-06-17"


def _registry():
    return get_style_registry()


def reload_dub_style_presets() -> None:
    global DUB_STYLE_PRESETS
    reload_style_registry()
    DUB_STYLE_PRESETS = get_style_registry().presets


def normalize_style_id(style_id: str | None) -> str:
    key = (style_id or DEFAULT_DUB_STYLE).strip().lower()
    key = LEGACY_STYLE_ALIASES.get(key, key)
    reg = _registry()
    if reg.get(key):
        return key
    return DEFAULT_DUB_STYLE


def get_dub_style(style_id: str | None) -> DubStylePreset:
    reg = _registry()
    sid = normalize_style_id(style_id)
    preset = reg.get(sid)
    if preset:
        return preset
    fallback = reg.get(DEFAULT_DUB_STYLE)
    if fallback:
        return fallback
    raise KeyError(f"Style not found: {style_id}")


def list_dub_styles(
    *,
    target_lang: str | None = None,
    local_only: bool = True,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    reg = _registry()
    presets, _ = reg.styles_for_target(
        target_lang, local_only=local_only, include_hidden=include_hidden
    )
    return [p.to_public_dict() for p in presets]


def list_style_pack_meta(
    target_lang: str | None = None,
    *,
    local_only: bool = True,
) -> dict[str, Any]:
    reg = _registry()
    _, region_id = reg.styles_for_target(target_lang, local_only=local_only)
    return {
        "target_lang": (target_lang or "").split("-")[0].lower() or None,
        "local_only": local_only,
        "regional_pack": region_id,
        "sections": reg.list_sections(target_lang, local_only=local_only),
        "available_regional_packs": sorted(
            {p.region_pack for p in reg.all_regional_styles() if p.region_pack}
        ),
    }


def get_preview_phrase(style_id: str | None, lang: str | None = None) -> str:
    preset = get_dub_style(style_id)
    phrase = preset.preview_phrase(lang)
    if phrase:
        return phrase
    return preset.preview_phrase("default") or "TubeDub style preview."


def resolve_dub_style(
    style_id: str | None,
    *,
    original_volume: float | None = None,
) -> dict[str, Any]:
    preset = get_dub_style(style_id)
    orig = preset.original_volume
    if original_volume is not None:
        orig = max(0.0, min(1.0, float(original_volume)))

    if preset.skip_tts:
        mix_mode = "original_only"
        dub_vol = 0.0
    elif preset.mix_mode == "full_dub" and orig <= 0.001:
        mix_mode = "full_dub"
        dub_vol = preset.dub_volume
    else:
        mix_mode = "custom" if preset.mix_mode != "original_only" else "original_only"
        dub_vol = preset.dub_volume

    return {
        "style_id": preset.id,
        "mix_mode": mix_mode,
        "mix_volumes": {
            "original_volume": orig,
            "dub_volume": dub_vol,
            "background_volume": orig,
        },
        "skip_tts": preset.skip_tts,
        "tts_rate": preset.tts_rate,
        "tts_pitch": preset.tts_pitch,
        "reply_start_delay_ms": preset.reply_start_delay_ms,
        "reply_start_delay_jitter_ms": preset.reply_start_delay_jitter_ms,
        "max_atempo": preset.max_atempo,
        "allow_atempo": preset.allow_atempo,
        "prefer_semantic_adapt": preset.prefer_semantic_adapt,
        "sync_mode": preset.sync_mode,
        "voice_fx": preset.voice_fx,
        "pack": preset.pack,
        "region_pack": preset.region_pack,
    }


def build_subtitle_segments(
    translated_segments: list[str],
    timing_map: list[Any],
) -> list:
    from engines.subtitle_formats import SubtitleSegment

    out = []
    n = min(len(translated_segments), len(timing_map) if timing_map else 0)
    for i in range(n):
        text = str(translated_segments[i] or "").strip()
        if not text:
            continue
        item = timing_map[i]
        if isinstance(item, dict):
            start_ms = int(item.get("start", 0))
            end_ms = int(item.get("end", start_ms + 3000))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start_ms, end_ms = int(item[0]), int(item[1])
        else:
            start_ms, end_ms = i * 3000, (i + 1) * 3000
        out.append(
            SubtitleSegment(index=len(out) + 1, start_ms=start_ms, end_ms=end_ms, text=text)
        )
    return out


DUB_STYLE_PRESETS: dict[str, DubStylePreset] = get_style_registry().presets
