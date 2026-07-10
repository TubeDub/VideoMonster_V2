"""Style Pack loader — base + regional JSON profiles (no per-language code branches)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.dub_style_loader")

_STYLES_ROOT = Path(__file__).resolve().parent.parent / "styles"
_LEGACY_DIR = Path(__file__).resolve().parent.parent / "data" / "dub_styles"


@dataclass(frozen=True)
class DubStylePreset:
    id: str
    mix_mode: str
    original_volume: float
    dub_volume: float
    background_volume: float
    skip_tts: bool = False
    tts_rate: str | None = None
    tts_pitch: str | None = None
    i18n_key: str = ""
    order: int = 0
    visible: bool = True
    pack: str = "base"
    region_pack: str | None = None
    reply_start_delay_ms: int = 0
    reply_start_delay_jitter_ms: int = 0
    max_atempo: float = 1.18
    allow_atempo: bool = True
    prefer_semantic_adapt: bool = False
    sync_mode: str = "standard"
    delivery: str = ""
    intonation_range: str = ""
    voice_fx: dict[str, Any] | None = None
    preview_phrases: dict[str, str] = field(default_factory=dict)

    @property
    def is_regional(self) -> bool:
        return self.pack == "regional"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mix_mode": self.mix_mode,
            "original_volume": self.original_volume,
            "original_volume_pct": int(round(self.original_volume * 100)),
            "dub_volume": self.dub_volume,
            "background_volume": self.background_volume,
            "skip_tts": self.skip_tts,
            "tts_rate": self.tts_rate,
            "tts_pitch": self.tts_pitch,
            "i18n_key": self.i18n_key,
            "order": self.order,
            "visible": self.visible,
            "pack": self.pack,
            "region_pack": self.region_pack,
            "is_regional": self.is_regional,
            "reply_start_delay_ms": self.reply_start_delay_ms,
            "max_atempo": self.max_atempo,
            "allow_atempo": self.allow_atempo,
            "prefer_semantic_adapt": self.prefer_semantic_adapt,
            "sync_mode": self.sync_mode,
            "delivery": self.delivery,
            "intonation_range": self.intonation_range,
            "has_voice_fx": bool(self.voice_fx),
            "preview_available": bool(self.preview_phrases) and not self.skip_tts,
        }

    def preview_phrase(self, lang: str | None = None) -> str:
        phrases = self.preview_phrases or {}
        if not phrases:
            return ""
        key = (lang or "default").split("-")[0].lower()
        return (
            phrases.get(key)
            or phrases.get(lang or "")
            or phrases.get("default")
            or next(iter(phrases.values()), "")
        )


from engines.utils.lang_utils import normalize_lang as _normalize_lang_core


def _normalize_lang(code: str | None) -> str:
    return _normalize_lang_core(code, default="")


def _parse_preset(data: dict[str, Any], *, default_region: str | None = None) -> DubStylePreset | None:
    try:
        sid = str(data.get("id") or "").strip()
        if not sid:
            return None
        pack = str(data.get("pack") or ("regional" if default_region else "base"))
        region = data.get("region_pack") or default_region
        if pack == "regional" and not region:
            region = default_region
        return DubStylePreset(
            id=sid,
            mix_mode=str(data.get("mix_mode") or "custom"),
            original_volume=float(data.get("original_volume", 0.0)),
            dub_volume=float(data.get("dub_volume", 1.0)),
            background_volume=float(data.get("background_volume", 0.0)),
            skip_tts=bool(data.get("skip_tts", False)),
            tts_rate=data.get("tts_rate"),
            tts_pitch=data.get("tts_pitch"),
            i18n_key=str(data.get("i18n_key") or f"dub.style_{sid}"),
            order=int(data.get("order", 99)),
            visible=bool(data.get("visible", True)),
            pack=pack,
            region_pack=str(region) if region else None,
            reply_start_delay_ms=int(data.get("reply_start_delay_ms", 0)),
            reply_start_delay_jitter_ms=int(data.get("reply_start_delay_jitter_ms", 0)),
            max_atempo=float(data.get("max_atempo", 1.18)),
            allow_atempo=bool(data.get("allow_atempo", True)),
            prefer_semantic_adapt=bool(data.get("prefer_semantic_adapt", False)),
            sync_mode=str(data.get("sync_mode") or "standard"),
            delivery=str(data.get("delivery") or ""),
            intonation_range=str(data.get("intonation_range") or ""),
            voice_fx=data.get("voice_fx") if isinstance(data.get("voice_fx"), dict) else None,
            preview_phrases=dict(data.get("preview_phrases") or {}),
        )
    except (TypeError, ValueError) as e:
        logger.warning("Skip invalid style profile: %s", e)
        return None


def _load_json_dir(directory: Path, *, default_region: str | None = None) -> dict[str, DubStylePreset]:
    out: dict[str, DubStylePreset] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read %s: %s", path, e)
            continue
        preset = _parse_preset(data, default_region=default_region)
        if preset:
            if preset.id in out:
                logger.warning("Duplicate style id %s — keeping first", preset.id)
                continue
            out[preset.id] = preset
    return out


def load_regional_map(styles_root: Path | None = None) -> dict[str, str]:
    root = styles_root or _STYLES_ROOT
    path = root / "regional_map.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("regional_map.json unreadable: %s", e)
        return {}


class StylePackRegistry:
    """In-memory registry of all style packs (base + regional)."""

    def __init__(self, styles_root: Path | None = None):
        self.styles_root = styles_root or _STYLES_ROOT
        self.regional_map = load_regional_map(self.styles_root)
        self.presets: dict[str, DubStylePreset] = {}
        self._reload()

    def _reload(self) -> None:
        merged: dict[str, DubStylePreset] = {}
        base_dir = self.styles_root / "base"
        regional_root = self.styles_root / "regional"

        merged.update(_load_json_dir(base_dir))

        if regional_root.is_dir():
            for region_dir in sorted(regional_root.iterdir()):
                if region_dir.is_dir():
                    merged.update(_load_json_dir(region_dir, default_region=region_dir.name))

        if not merged and _LEGACY_DIR.is_dir():
            logger.info("Style packs: falling back to legacy %s", _LEGACY_DIR)
            merged.update(_load_json_dir(_LEGACY_DIR))

        self.presets = merged

    def reload(self) -> None:
        self.regional_map = load_regional_map(self.styles_root)
        self._reload()

    def get(self, style_id: str) -> DubStylePreset | None:
        return self.presets.get(style_id)

    def regional_pack_for_lang(self, target_lang: str | None) -> str | None:
        code = _normalize_lang(target_lang)
        if not code:
            return None
        return self.regional_map.get(code)

    def base_styles(self, *, include_hidden: bool = False) -> list[DubStylePreset]:
        items = [p for p in self.presets.values() if p.pack == "base"]
        if not include_hidden:
            items = [p for p in items if p.visible]
        return sorted(items, key=lambda p: p.order)

    def regional_styles(
        self, region_pack: str, *, include_hidden: bool = False
    ) -> list[DubStylePreset]:
        items = [
            p
            for p in self.presets.values()
            if p.pack == "regional" and p.region_pack == region_pack
        ]
        if not include_hidden:
            items = [p for p in items if p.visible]
        return sorted(items, key=lambda p: p.order)

    def all_regional_styles(self, *, include_hidden: bool = False) -> list[DubStylePreset]:
        items = [p for p in self.presets.values() if p.pack == "regional"]
        if not include_hidden:
            items = [p for p in items if p.visible]
        return sorted(items, key=lambda p: (p.region_pack or "", p.order))

    def styles_for_target(
        self,
        target_lang: str | None,
        *,
        local_only: bool = True,
        include_hidden: bool = False,
    ) -> tuple[list[DubStylePreset], str | None]:
        """Return styles list + active regional pack id (if any)."""
        base = self.base_styles(include_hidden=include_hidden)
        region_id = self.regional_pack_for_lang(target_lang)

        if local_only:
            regional = self.regional_styles(region_id, include_hidden=include_hidden) if region_id else []
            return base + regional, region_id

        return base + self.all_regional_styles(include_hidden=include_hidden), region_id

    def list_sections(
        self,
        target_lang: str | None,
        *,
        local_only: bool = True,
    ) -> list[dict[str, str]]:
        sections = [{"id": "base", "label_key": "dub.styles_section_base"}]
        if local_only:
            region_id = self.regional_pack_for_lang(target_lang)
            if region_id:
                sections.append(
                    {
                        "id": region_id,
                        "label_key": f"dub.styles_section_{region_id}",
                    }
                )
        else:
            seen: set[str] = set()
            for p in self.all_regional_styles():
                rid = p.region_pack or "regional"
                if rid not in seen:
                    seen.add(rid)
                    sections.append(
                        {"id": rid, "label_key": f"dub.styles_section_{rid}"}
                    )
        return sections


_REGISTRY: StylePackRegistry | None = None


def get_style_registry() -> StylePackRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = StylePackRegistry()
    return _REGISTRY


def reload_style_registry() -> StylePackRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = StylePackRegistry()
    else:
        _REGISTRY.reload()
    return _REGISTRY


def load_dub_style_presets(styles_dir: Path | None = None) -> dict[str, DubStylePreset]:
    """Backward-compatible flat dict of all presets."""
    if styles_dir:
        reg = StylePackRegistry(styles_dir.parent if styles_dir.name == "dub_styles" else styles_dir)
    else:
        reg = get_style_registry()
    return dict(reg.presets)
