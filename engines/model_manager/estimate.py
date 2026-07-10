"""Download size estimates from Router plan."""

from __future__ import annotations

from pathlib import Path

from engines.model_manager.downloader import is_component_ready, is_mt_engine_ready
from engines.model_manager.profiles import profile_for_pair

_WHISPER_MB: dict[str, int] = {
    "tiny": 80,
    "base": 150,
    "small": 500,
    "medium": 1500,
    "large": 3000,
}
_ENGINE_MB: dict[str, int] = {
    "marian": 350,
    "argos": 80,
    "nllb": 1200,
}
_COMPONENT_MB: dict[str, int] = {
    "tts": 0,
    "naturalizer": 0,
    "ocr": 200,
}


def estimate_profile_download_mb(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
    *,
    whisper_size: str = "tiny",
    ocr_enabled: bool = False,
    feature: str = "dub",
) -> float:
    items = profile_for_pair(
        app_dir,
        source_lang,
        target_lang,
        whisper_size=whisper_size,
        ocr_enabled=ocr_enabled,
        feature=feature,
    )
    total = 0.0
    seen_nllb = False
    for item in items:
        if is_component_ready(
            app_dir,
            item.component_id,
            item.variant,
            engine_id=item.engine_id,
            src_lang=item.src_lang,
            tgt_lang=item.tgt_lang,
        ):
            continue
        if item.component_id == "whisper":
            total += _WHISPER_MB.get(item.variant, 150)
        elif item.component_id == "mt":
            total += _ENGINE_MB.get("marian", 350)
        else:
            total += _COMPONENT_MB.get(item.component_id, 50)
    return round(total, 1)
