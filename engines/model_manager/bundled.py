"""Pre-installed language packs (en ↔ ru ↔ uk) shipped with the installer."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.model_manager.bundled")

_MANIFEST: dict | None = None


def _manifest_path(app_dir: Path) -> Path:
    return app_dir / "data" / "bundled_language_packs.json"


def load_bundled_manifest(app_dir: Path) -> dict:
    global _MANIFEST
    if _MANIFEST is not None:
        return _MANIFEST
    path = _manifest_path(app_dir)
    if not path.is_file():
        _MANIFEST = {"pairs": [], "whisper_sizes": ["tiny"], "marian_models": []}
        return _MANIFEST
    try:
        _MANIFEST = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _MANIFEST = {"pairs": [], "whisper_sizes": ["tiny"], "marian_models": []}
    return _MANIFEST


def bundled_pairs(app_dir: Path) -> list[tuple[str, str]]:
    data = load_bundled_manifest(app_dir)
    out: list[tuple[str, str]] = []
    for row in data.get("pairs") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            out.append((normalize_lang(row[0]), normalize_lang(row[1])))
    return out


def is_bundled_pair(app_dir: Path, source_lang: str, target_lang: str) -> bool:
    src = normalize_lang(source_lang or "en")
    tgt = normalize_lang(target_lang or "ru")
    if src == tgt:
        return True
    return (src, tgt) in bundled_pairs(app_dir)


def register_bundled_components(app_dir: Path) -> dict:
    """Mark bundled models found in local cache (shipped with installer). Fast — no Argos/router."""
    from engines.model_manager.downloader import is_component_ready
    from engines.model_manager.registry import touch_component

    data = load_bundled_manifest(app_dir)
    registered: list[str] = []

    for size in data.get("whisper_sizes") or ["tiny"]:
        if is_component_ready(app_dir, "whisper", size):
            touch_component(
                app_dir,
                "whisper",
                size,
                engine_hint="whisper",
                artifact_id=f"whisper-{size}",
            )
            registered.append(f"whisper:{size}")

    for mid in data.get("marian_models") or []:
        from engines.model_manager.integrity import verify_hf_model

        if not verify_hf_model(app_dir, mid):
            continue
        pair = mid.split("opus-mt-")[-1]
        parts = pair.split("-", 1)
        if len(parts) != 2:
            continue
        src, tgt = parts[0], parts[1]
        touch_component(
            app_dir,
            "mt",
            f"{src}-{tgt}",
            engine_hint="marian",
            artifact_id=mid,
        )
        registered.append(f"mt:{src}-{tgt}")
        touch_component(app_dir, "naturalizer", tgt, engine_hint="bundled")

    if registered:
        logger.info("Bundled language packs registered: %s", ", ".join(registered[:12]))
    return {"registered": registered, "bundled_pair_count": len(bundled_pairs(app_dir))}


def bundled_translate_ready(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
    *,
    whisper_size: str = "tiny",
    feature: str = "translate",
) -> bool:
    """True when a bundled pair has all required local components (no download)."""
    from engines.model_manager.profiles import profile_for_pair

    if not is_bundled_pair(app_dir, source_lang, target_lang):
        return False
    items = profile_for_pair(
        app_dir,
        source_lang,
        target_lang,
        whisper_size=whisper_size,
        feature=feature,
    )
    from engines.model_manager.downloader import is_component_ready

    for item in items:
        if not is_component_ready(
            app_dir,
            item.component_id,
            item.variant,
            engine_id=item.engine_id,
            src_lang=item.src_lang,
            tgt_lang=item.tgt_lang,
        ):
            return False
    return True
