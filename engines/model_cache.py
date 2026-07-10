"""Backward-compatible shim — use engines.model_manager instead."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.model_manager import (
    apply_cleanup,
    configure,
    delete_artifact as delete_model,
    get_storage_status,
    list_components as list_models,
    set_max_storage_gb,
    set_storage_root,
    suggest_cleanup,
)
from engines.model_manager.config import load_config
from engines.model_manager.downloader import ensure_mt, ensure_whisper, is_component_ready
from engines.model_manager.integrity import cleanup_temp_files as cleanup_temp_and_corrupted
from engines.model_manager.integrity import verify_hf_model as model_is_local
from engines.model_manager.registry import touch_component
from engines.model_manager.storage import hub_dir as hf_hub_dir
from engines.model_manager.storage import get_storage_root as hf_root
from engines.model_manager.storage import is_configured
from engines.model_manager.storage import tmp_dir as hf_tmp_dir
from engines.model_manager.storage import transformers_dir as hf_transformers_dir
from engines.model_manager.storage import hub_dir

DEFAULT_MAX_CACHE_GB = 10.0


def cache_status(app_dir: Path) -> dict[str, Any]:
    st = get_storage_status(app_dir)
    return {
        "hf_home": st.get("storage_root", ""),
        "cache_bytes": st.get("total_bytes", 0),
        "cache_mb": st.get("total_mb", 0),
        "cache_gb": st.get("total_gb", 0),
        "max_cache_gb": st.get("max_storage_gb", 10),
        "disk_free_gb": st.get("disk_free_gb", -1),
        "disk_total_gb": st.get("disk_total_gb", -1),
        "model_count": st.get("component_count", 0),
        "models": list_models(app_dir),
        "configured": is_configured(),
        "pipeline_cache_mb": st.get("pipeline_cache_mb", 0),
    }


def touch_model(app_dir: Path, model_id: str, *, engine: str = "", bytes_hint: int = 0) -> None:
    touch_component(app_dir, "mt", model_id.split("/")[-1], engine_hint=engine, artifact_id=model_id)


def set_max_cache_gb(app_dir: Path, gb: float) -> None:
    set_max_storage_gb(app_dir, gb)


def whisper_download_root(app_dir: Path):
    return hub_dir(app_dir)


def configure_hf_cache(app_dir: Path, *, run_cleanup: bool = True):
    return configure(app_dir, run_temp_cleanup=run_cleanup)


def max_cache_bytes(app_dir: Path) -> int:
    cfg = load_config(app_dir)
    return int(float(cfg.get("max_storage_gb", DEFAULT_MAX_CACHE_GB)) * 1024**3)


def enforce_size_limit(app_dir: Path, *, max_bytes: int | None = None, confirmed: bool = False) -> dict[str, Any]:
    from engines.model_manager import apply_lru_if_allowed

    return apply_lru_if_allowed(app_dir, confirmed=confirmed)


def models_needed_for_pair(app_dir: Path, src_lang: str, tgt_lang: str) -> list[str]:
    from engines.mt.lang_codes import normalize_lang, pair_key
    from engines.mt.registry import load_pair_rankings

    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    if src == tgt:
        return []
    pk = pair_key(src, tgt)
    rankings = load_pair_rankings(app_dir).get(pk, [])
    needed: list[str] = []
    for eng_id in rankings[:2]:
        if eng_id == "marian":
            needed.append(f"Helsinki-NLP/opus-mt-{src}-{tgt}")
        elif eng_id == "nllb":
            needed.append("facebook/nllb-200-distilled-600M")
    if not needed and rankings and rankings[0] == "marian":
        needed.append(f"Helsinki-NLP/opus-mt-{src}-{tgt}")
    return needed


def redownload_model(app_dir: Path, model_id: str, *, engine: str = "marian") -> dict[str, Any]:
    from engines.model_manager import update_component

    if engine == "whisper" or "whisper" in model_id.lower():
        delete_model(app_dir, model_id)
        ensure_whisper(app_dir, model_id.replace("whisper-", ""))
        return {"ok": True}
    if engine == "argos":
        parts = model_id.replace("argos-", "").split("-", 1)
        if len(parts) == 2:
            delete_model(app_dir, model_id)
            ensure_mt(app_dir, parts[0], parts[1])
            return {"ok": True}
    delete_model(app_dir, model_id)
    parts = model_id.split("opus-mt-")[-1].split("-", 1) if "opus-mt" in model_id else []
    if len(parts) == 2:
        ensure_mt(app_dir, parts[0], parts[1])
    return {"ok": True, "model_id": model_id}


def ensure_models_for_pair(app_dir: Path, src_lang: str, tgt_lang: str) -> list[str]:
    return models_needed_for_pair(app_dir, src_lang, tgt_lang)
