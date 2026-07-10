"""
ModelManager — единая точка управления AI-компонентами VideoMonster V2.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from engines.model_manager import downloader, profiles, registry, storage
from engines.model_manager.config import load_config, save_config
from engines.model_manager.downloader import DiskSpaceError, is_component_ready
from engines.model_manager.labels import label
from engines.model_manager.profiles import ProfileItem, profile_for_pair, route_plan_for_pair
from engines.model_manager.registry import (
    lru_candidates,
    load_registry,
    save_registry,
    scan_all_components,
    suggest_cleanup,
    touch_component,
)


@dataclass
class PrepareProgress:
    phase: str
    component_id: str
    label: str
    percent: float
    detail: str = ""


@dataclass
class PrepareResult:
    ready: bool
    components: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_code: str = ""


def configure(app_dir: Path, *, run_temp_cleanup: bool = True) -> Path:
    root = storage.configure(app_dir, run_temp_cleanup=run_temp_cleanup)
    try:
        from engines.model_manager.bundled import register_bundled_components

        register_bundled_components(app_dir)
    except Exception:
        pass
    return root


def plan_route(app_dir: Path, source_lang: str, target_lang: str):
    return route_plan_for_pair(app_dir, source_lang, target_lang)


def is_profile_ready(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
    *,
    whisper_size: str = "tiny",
    ocr_enabled: bool = False,
    feature: str = "dub",
) -> bool:
    items = profile_for_pair(
        app_dir,
        source_lang,
        target_lang,
        whisper_size=whisper_size,
        ocr_enabled=ocr_enabled,
        feature=feature,
    )
    for i in items:
        if not is_component_ready(
            app_dir,
            i.component_id,
            i.variant,
            engine_id=i.engine_id,
            src_lang=i.src_lang,
            tgt_lang=i.tgt_lang,
        ):
            return False
    return True


def _user_prepare_error(code: str, dev_detail: str = "") -> str:
    if code == "disk_full":
        return dev_detail
    return "Не удалось подготовить компоненты. Подробности — в Dev Log (output/dev/prepare_latest.log)."


def ensure_profile(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
    *,
    whisper_size: str = "tiny",
    ocr_enabled: bool = False,
    feature: str = "dub",
    ui_lang: str = "ru",
    progress_cb: Callable[[PrepareProgress], None] | None = None,
    job_id: str = "",
    warmup: bool | None = None,
) -> PrepareResult:
    from engines.mt.lang_codes import normalize_lang
    from engines.model_manager.prepare_dev_log import log_prepare_plan
    from engines.model_manager.runtime import prepare_download_session

    src = normalize_lang(source_lang or "en")
    tgt = normalize_lang(target_lang or "ru")
    plan = route_plan_for_pair(app_dir, src, tgt)
    items = profile_for_pair(
        app_dir,
        src,
        tgt,
        whisper_size=whisper_size,
        ocr_enabled=ocr_enabled,
        feature=feature,
    )
    dev_events: list[str] = []
    total_w = sum(i.weight for i in items) or 1.0
    done_w = 0.0
    results: list[dict[str, Any]] = []

    log_prepare_plan(
        app_dir,
        source_lang=src,
        target_lang=tgt,
        plan=plan,
        events=[f"profile_items={len(items)}"],
        job_id=job_id,
    )

    def _emit(phase: str, comp: ProfileItem, pct: float, detail: str = "") -> None:
        if progress_cb:
            progress_cb(
                PrepareProgress(
                    phase=phase,
                    component_id=comp.component_id,
                    label=label(comp.component_id, ui_lang),
                    percent=round(pct, 1),
                    detail=detail,
                )
            )

    def _download_detail(comp: ProfileItem) -> str:
        hints_ru = {
            "whisper": "загрузка модели (~75 МБ при первом запуске, 1–3 мин)",
            "mt": "загрузка модели перевода (~300 МБ, 2–5 мин)",
            "tts": "проверка голосового движка",
            "naturalizer": "настройка пост-обработки текста",
            "ocr": "загрузка OCR-модуля",
        }
        hints_en = {
            "whisper": "downloading speech model (~75 MB first run, 1–3 min)",
            "mt": "downloading translation model (~300 MB, 2–5 min)",
            "tts": "checking voice engine",
            "naturalizer": "configuring text post-processing",
            "ocr": "downloading OCR module",
        }
        lang = (ui_lang or "ru").split("-")[0]
        table = hints_en if lang == "en" else hints_ru
        return table.get(comp.component_id, "")

    try:
        with prepare_download_session():
            for item in items:
                _emit("check", item, (done_w / total_w) * 100)
                ready = is_component_ready(
                    app_dir,
                    item.component_id,
                    item.variant,
                    engine_id=item.engine_id,
                    src_lang=item.src_lang,
                    tgt_lang=item.tgt_lang,
                )
                if ready:
                    results.append({"id": item.component_id, "variant": item.variant, "status": "ready"})
                    done_w += item.weight
                    _emit("ready", item, (done_w / total_w) * 100)
                    continue

                _emit("download", item, max((done_w / total_w) * 100, 1.0), _download_detail(item))
                if item.component_id == "whisper":
                    downloader.ensure_whisper(app_dir, item.variant)
                elif item.component_id == "mt" and item.src_lang and item.tgt_lang:
                    leg_result = downloader.ensure_mt_leg(app_dir, item.src_lang, item.tgt_lang)
                    dev_events.extend(leg_result.get("notes") or [])
                elif item.component_id == "mt":
                    parts = item.variant.split("-", 1)
                    if len(parts) == 2:
                        leg_result = downloader.ensure_mt_leg(app_dir, parts[0], parts[1])
                        dev_events.extend(leg_result.get("notes") or [])
                elif item.component_id == "tts":
                    downloader.ensure_tts(app_dir, item.variant)
                elif item.component_id == "naturalizer":
                    touch_component(app_dir, "naturalizer", item.variant, engine_hint="llm")
                else:
                    touch_component(app_dir, item.component_id, item.variant)

                results.append({"id": item.component_id, "variant": item.variant, "status": "downloaded"})
                done_w += item.weight
                _emit("verify", item, (done_w / total_w) * 100)

            import os as _os

            do_warmup = warmup
            if do_warmup is None:
                do_warmup = _os.getenv("VM_PREPARE_WARMUP", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
            if do_warmup:
                _emit("warmup", ProfileItem("mt", "plan", 0.1), 95.0)
                if plan.prepare_legs:
                    downloader.preload_route_plan(app_dir, plan)
                from engines.model_manager.integrity import verify_whisper

                whisper_item = next((i for i in items if i.component_id == "whisper"), None)
                if whisper_item and verify_whisper(app_dir, whisper_item.variant):
                    downloader.load_whisper(app_dir, whisper_item.variant)
            else:
                dev_events.append("warmup skipped (load on first dub step)")

        dev_events.append("prepare complete")
        log_prepare_plan(
            app_dir,
            source_lang=src,
            target_lang=tgt,
            plan=plan,
            events=dev_events,
            job_id=job_id,
        )
        if progress_cb:
            progress_cb(PrepareProgress("done", "", label("mt", ui_lang), 100.0, ""))
        return PrepareResult(ready=True, components=results)

    except DiskSpaceError as e:
        msg = f"Недостаточно места: нужно {e.required_mb:.0f} МБ, свободно {e.free_mb:.0f} МБ"
        dev_events.append(msg)
        log_prepare_plan(app_dir, source_lang=src, target_lang=tgt, plan=plan, events=dev_events, job_id=job_id)
        return PrepareResult(ready=False, components=results, error=msg, error_code="disk_full")
    except Exception as e:
        dev_events.append(str(e))
        log_prepare_plan(app_dir, source_lang=src, target_lang=tgt, plan=plan, events=dev_events, job_id=job_id)
        return PrepareResult(
            ready=False,
            components=results,
            error=_user_prepare_error("prepare_failed", str(e)),
            error_code="prepare_failed",
        )


def list_components(app_dir: Path) -> list[dict[str, Any]]:
    return scan_all_components(app_dir)


def get_storage_status(app_dir: Path) -> dict[str, Any]:
    cfg = load_config(app_dir)
    root = storage.get_storage_root(app_dir)
    comps = scan_all_components(app_dir)
    total = sum(c.get("bytes", 0) for c in comps)
    disk = storage.disk_usage_for_storage(app_dir)
    pipeline = app_dir / "output" / "cache" / "pipeline"

    return {
        "storage_root": str(root),
        "total_bytes": total,
        "total_mb": round(total / 1024**2, 1),
        "total_gb": round(total / 1024**3, 3),
        "max_storage_gb": float(cfg.get("max_storage_gb", 10)),
        "disk_free_gb": disk.get("free_gb", -1),
        "disk_total_gb": disk.get("total_gb", -1),
        "pipeline_cache_mb": round(storage.dir_size(pipeline) / 1024**2, 1),
        "last_cleanup": cfg.get("last_cleanup", ""),
        "component_count": len(comps),
        "lru_candidates": lru_candidates(app_dir),
        "require_confirm_lru": bool(cfg.get("require_confirm_before_lru", True)),
    }


def delete_artifact(app_dir: Path, artifact_id: str) -> dict:
    from engines.model_manager.integrity import model_id_to_folder

    if not artifact_id:
        return {"ok": False, "error": "not_found"}
    folder = storage.hub_dir(app_dir) / model_id_to_folder(artifact_id)
    if not folder.is_dir():
        folder = storage.hub_dir(app_dir) / artifact_id
    if not folder.is_dir():
        return {"ok": False, "error": "not_found"}
    sz = storage.dir_size(folder)
    shutil.rmtree(folder)
    reg = load_registry(app_dir)
    reg.get("models", {}).pop(artifact_id, None)
    save_registry(app_dir, reg)
    _clear_runtime_caches()
    return {"ok": True, "freed_bytes": sz}


def delete_component(app_dir: Path, component_id: str, variant: str, *, force: bool = False) -> dict:
    if not force:
        return {"ok": False, "error": "confirmation_required"}
    comps = scan_all_components(app_dir)
    target = next((c for c in comps if c["id"] == component_id and c["variant"] == variant), None)
    if not target or not target.get("path"):
        aid = target.get("artifact_id", "") if target else ""
        if aid:
            return delete_artifact(app_dir, aid)
        return {"ok": False, "error": "not_found"}
    path = Path(target["path"])
    if path.is_dir():
        shutil.rmtree(path)
    reg = load_registry(app_dir)
    reg.get("components", {}).pop(f"{component_id}:{variant}", None)
    save_registry(app_dir, reg)
    _clear_runtime_caches()
    return {"ok": True, "freed_bytes": target.get("bytes", 0)}


def update_component(app_dir: Path, component_id: str, variant: str) -> dict:
    delete_component(app_dir, component_id, variant, force=True)
    if component_id == "whisper":
        downloader.ensure_whisper(app_dir, variant)
    elif component_id == "mt":
        p = variant.split("-", 1)
        if len(p) == 2:
            downloader.ensure_mt(app_dir, p[0], p[1])
    return {"ok": True}


def apply_cleanup(app_dir: Path, keys: list[str], *, confirmed: bool = True) -> dict:
    if not confirmed:
        return {"ok": False, "error": "confirmation_required"}
    freed = 0
    deleted: list[str] = []
    for key in keys:
        if ":" in key:
            cid, var = key.split(":", 1)
            r = delete_component(app_dir, cid, var, force=True)
        else:
            r = delete_artifact(app_dir, key)
        if r.get("ok"):
            freed += r.get("freed_bytes", 0)
            deleted.append(key)

    cfg = load_config(app_dir)
    cfg["last_cleanup"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_config(app_dir, cfg)
    return {"ok": True, "deleted": deleted, "freed_bytes": freed}


def apply_lru_if_allowed(app_dir: Path, *, confirmed: bool = False) -> dict:
    cfg = load_config(app_dir)
    if cfg.get("require_confirm_before_lru", True) and not confirmed:
        return {"ok": False, "needs_confirm": True, "candidates": lru_candidates(app_dir)}
    keys = [f"{c['id']}:{c['variant']}" for c in lru_candidates(app_dir)]
    return apply_cleanup(app_dir, keys, confirmed=True)


def set_storage_root(app_dir: Path, new_root: Path) -> dict:
    return storage.set_storage_root(app_dir, new_root)


def set_max_storage_gb(app_dir: Path, gb: float) -> None:
    cfg = load_config(app_dir)
    cfg["max_storage_gb"] = max(0.5, float(gb))
    save_config(app_dir, cfg)


def list_drives() -> list[dict]:
    return storage.list_available_drives()


def _clear_runtime_caches() -> None:
    try:
        from engines.mt import marian_engine

        marian_engine._MODEL_CACHE.clear()
    except Exception:
        pass
    try:
        from engines.mt import nllb_engine

        nllb_engine._PIPELINE = None
    except Exception:
        pass
    downloader._WHISPER_CACHE.clear()
    downloader._MARIAN_CACHE.clear()
    downloader._NLLB_PIPELINE = None
