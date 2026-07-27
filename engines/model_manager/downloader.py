"""Unified download/load — sole entry for model fetching."""

from __future__ import annotations

import importlib.util
import logging
import shutil
from pathlib import Path

from engines.model_manager.integrity import verify_hf_model, verify_whisper
from engines.model_manager.registry import touch_component
from engines.model_manager.runtime import (
    ModelNotPreparedError,
    OfflineOnlyError,
    assert_downloads_allowed,
    downloads_permitted,
    is_offline_only,
)
from engines.model_manager.storage import hub_dir

logger = logging.getLogger("tubedub.model_manager.downloader")

_WHISPER_CACHE: dict = {}
_MARIAN_CACHE: dict = {}
_NLLB_PIPELINE = None
NLLB_MODEL_ID = "facebook/nllb-200-distilled-600M"


class DiskSpaceError(Exception):
    def __init__(self, required_mb: float, free_mb: float):
        self.required_mb = required_mb
        self.free_mb = free_mb
        super().__init__(f"Need {required_mb:.0f} MB, free {free_mb:.0f} MB")


def _check_disk(app_dir: Path, required_bytes: int = 200_000_000) -> None:
    root = hub_dir(app_dir)
    try:
        usage = shutil.disk_usage(str(root.anchor or root))
        if usage.free < required_bytes + 50_000_000:
            raise DiskSpaceError(required_bytes / 1024**2, usage.free / 1024**2)
    except DiskSpaceError:
        raise
    except Exception:
        pass


def _has_package(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _local_kw() -> dict:
    return {"local_files_only": True}


def is_mt_engine_ready(app_dir: Path, engine_id: str, src: str, tgt: str) -> bool:
    if engine_id == "argos":
        return _argos_ready(src, tgt)
    if engine_id == "nllb":
        if not (_has_package("transformers") and _has_package("torch")):
            return False
        return verify_hf_model(app_dir, NLLB_MODEL_ID)
    if engine_id == "marian":
        # Honest gate: model weights alone are useless without torch/transformers.
        if not (_has_package("transformers") and _has_package("torch")):
            return False
        return verify_hf_model(app_dir, f"Helsinki-NLP/opus-mt-{src}-{tgt}")
    return True


def is_component_ready(
    app_dir: Path,
    component_id: str,
    variant: str,
    *,
    engine_id: str = "",
    src_lang: str = "",
    tgt_lang: str = "",
) -> bool:
    if component_id == "whisper":
        return verify_whisper(app_dir, variant)
    if component_id == "mt":
        if src_lang and tgt_lang:
            return is_mt_leg_ready(app_dir, src_lang, tgt_lang)
        if engine_id and src_lang and tgt_lang:
            return is_mt_engine_ready(app_dir, engine_id, src_lang, tgt_lang)
        if ":" in variant:
            eng, pair = variant.split(":", 1)
            parts = pair.split("-", 1)
            if len(parts) == 2:
                return is_mt_engine_ready(app_dir, eng, parts[0], parts[1])
        parts = variant.split("-", 1)
        if len(parts) == 2:
            return is_mt_leg_ready(app_dir, parts[0], parts[1])
        return False
    if component_id == "tts":
        return _has_package("edge_tts")
    if component_id in ("naturalizer", "semantic", "router"):
        return True
    if component_id == "ocr":
        return _has_package("easyocr") or _has_package("paddleocr")
    return True


def _argos_ready(src: str, tgt: str) -> bool:
    try:
        import argostranslate.package as pkg

        installed = pkg.get_installed_packages()
        return any(p.from_code == src and p.to_code == tgt for p in installed)
    except Exception:
        return False


def argos_pair_available(src: str, tgt: str) -> bool:
    """Installed or listed in Argos index (no download)."""
    if _argos_ready(src, tgt):
        return True
    try:
        import argostranslate.package as pkg

        return any(
            p.from_code == src and p.to_code == tgt for p in pkg.get_available_packages()
        )
    except Exception:
        return False


def is_mt_leg_ready(app_dir: Path, src: str, tgt: str) -> bool:
    from engines.mt.lang_codes import pair_key
    from engines.mt.registry import load_pair_rankings

    pk = pair_key(src, tgt)
    rankings = load_pair_rankings(app_dir).get(pk, ["marian", "argos"])
    for eng in rankings:
        if eng not in ("marian", "argos", "nllb"):
            continue
        if eng == "argos" and not argos_pair_available(src, tgt):
            continue
        if is_mt_engine_ready(app_dir, eng, src, tgt):
            return True
    return is_mt_engine_ready(app_dir, "marian", src, tgt)


def ensure_whisper(app_dir: Path, size: str) -> None:
    if verify_whisper(app_dir, size):
        touch_component(app_dir, "whisper", size, engine_hint="whisper", artifact_id=f"whisper-{size}")
        return
    assert_downloads_allowed("whisper")
    _check_disk(app_dir, 150_000_000)
    from faster_whisper import WhisperModel

    root = str(hub_dir(app_dir))
    logger.info("[ModelManager] download whisper %s", size)
    WhisperModel(size, device="cpu", compute_type="int8", download_root=root)
    touch_component(app_dir, "whisper", size, engine_hint="whisper", artifact_id=f"whisper-{size}")


def ensure_mt_engine(app_dir: Path, engine_id: str, src: str, tgt: str) -> None:
    if is_mt_engine_ready(app_dir, engine_id, src, tgt):
        variant = f"{engine_id}:{src}-{tgt}"
        artifact = (
            f"argos-{src}-{tgt}"
            if engine_id == "argos"
            else NLLB_MODEL_ID
            if engine_id == "nllb"
            else f"Helsinki-NLP/opus-mt-{src}-{tgt}"
        )
        touch_component(app_dir, "mt", variant, engine_hint=engine_id, artifact_id=artifact)
        return

    if engine_id == "argos":
        _ensure_argos(app_dir, src, tgt)
        return
    if engine_id == "nllb":
        _ensure_nllb(app_dir, src, tgt)
        return
    if engine_id == "marian":
        _ensure_marian(app_dir, src, tgt)
        return

    raise RuntimeError(f"Unknown MT engine: {engine_id}")


def ensure_mt_leg(app_dir: Path, src: str, tgt: str) -> dict:
    """Ensure primary MT engine for a route leg (one download, no fallback chain)."""
    from engines.mt.registry import engines_for_pair

    notes: list[str] = []
    primary, _fallback = engines_for_pair(app_dir, src, tgt)
    eng = primary
    if eng == "argos" and not argos_pair_available(src, tgt):
        eng = "marian"
        notes.append(f"argos {src}->{tgt} unavailable — use marian")
    ensure_mt_engine(app_dir, eng, src, tgt)
    notes.append(f"ok {eng} {src}->{tgt}")
    # CJK→uk/ru: also prepare English pivot legs (offline Argos zh→en + Marian en→uk)
    if src in ("zh", "ja", "ko") and tgt in ("uk", "ru", "en"):
        try:
            if tgt != "en":
                ensure_mt_engine(app_dir, "argos", src, "en")
                notes.append(f"ok pivot {src}->en (argos)")
                ensure_mt_engine(app_dir, "marian", "en", tgt)
                notes.append(f"ok pivot en->{tgt} (marian)")
        except Exception as exc:
            notes.append(f"pivot prep skipped: {exc}")
    return {"ok": True, "engine": eng, "notes": notes}

def ensure_mt(app_dir: Path, src: str, tgt: str) -> None:
    """Legacy: ensure primary ranked engine for pair."""
    ensure_mt_leg(app_dir, src, tgt)


def _ensure_marian(app_dir: Path, src: str, tgt: str) -> None:
    mid = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
    if verify_hf_model(app_dir, mid):
        touch_component(app_dir, "mt", f"marian:{src}-{tgt}", engine_hint="marian", artifact_id=mid)
        return
    assert_downloads_allowed("marian")
    _check_disk(app_dir, 400_000_000)
    from transformers import MarianMTModel, MarianTokenizer

    logger.info("[ModelManager] download mt %s", mid)
    MarianTokenizer.from_pretrained(mid)
    MarianMTModel.from_pretrained(mid)
    touch_component(app_dir, "mt", f"marian:{src}-{tgt}", engine_hint="marian", artifact_id=mid)


def _ensure_nllb(app_dir: Path, src: str, tgt: str) -> None:
    if verify_hf_model(app_dir, NLLB_MODEL_ID):
        touch_component(app_dir, "mt", f"nllb:{src}-{tgt}", engine_hint="nllb", artifact_id=NLLB_MODEL_ID)
        return
    assert_downloads_allowed("nllb")
    _check_disk(app_dir, 800_000_000)
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    AutoTokenizer.from_pretrained(NLLB_MODEL_ID)
    AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL_ID)
    touch_component(app_dir, "mt", f"nllb:{src}-{tgt}", engine_hint="nllb", artifact_id=NLLB_MODEL_ID)


def _ensure_argos(app_dir: Path, src: str, tgt: str) -> None:
    if _argos_ready(src, tgt):
        touch_component(app_dir, "mt", f"argos:{src}-{tgt}", engine_hint="argos", artifact_id=f"argos-{src}-{tgt}")
        return
    assert_downloads_allowed("argos")
    _check_disk(app_dir, 100_000_000)
    import argostranslate.package as pkg
    import argostranslate.translate  # noqa: F401

    available = pkg.get_available_packages()
    match = next((p for p in available if p.from_code == src and p.to_code == tgt), None)
    if not match:
        logger.info("[ModelManager] Argos package %s->%s not available — skip", src, tgt)
        raise RuntimeError(f"Argos package {src}->{tgt} not available")
    logger.info("[ModelManager] download argos %s->%s", src, tgt)
    pkg.install_from_path(match.download())
    touch_component(app_dir, "mt", f"argos:{src}-{tgt}", engine_hint="argos", artifact_id=f"argos-{src}-{tgt}")


def ensure_tts(app_dir: Path, lang: str) -> None:
    if not _has_package("edge_tts"):
        raise RuntimeError("edge-tts not installed")
    touch_component(app_dir, "tts", lang, engine_hint="edge-tts")


def preload_mt_engine(app_dir: Path, engine_id: str, src: str, tgt: str) -> None:
    """Warm runtime cache after ensure — still allowed during prepare."""
    if engine_id == "marian":
        load_marian(app_dir, src, tgt)
    elif engine_id == "nllb":
        load_nllb(app_dir)
    elif engine_id == "argos":
        load_argos_translator(app_dir, src, tgt)


def preload_route_plan(app_dir: Path, plan) -> None:
    seen_nllb = False
    for leg_src, leg_tgt in getattr(plan, "prepare_legs", []) or []:
        try:
            if is_mt_engine_ready(app_dir, "marian", leg_src, leg_tgt):
                load_marian(app_dir, leg_src, leg_tgt)
            for req in getattr(plan, "mt_requirements", []):
                if req.src != leg_src or req.tgt != leg_tgt:
                    continue
                if req.engine_id == "nllb":
                    if seen_nllb:
                        continue
                    seen_nllb = True
                    if is_mt_engine_ready(app_dir, "nllb", leg_src, leg_tgt):
                        load_nllb(app_dir)
                elif req.engine_id == "argos" and _argos_ready(req.src, req.tgt):
                    load_argos_translator(app_dir, req.src, req.tgt)
        except Exception as exc:
            logger.debug("[ModelManager] preload %s->%s: %s", leg_src, leg_tgt, exc)


def _is_whisper_oom(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    return any(
        tok in msg
        for tok in (
            "mkl_malloc",
            "failed to allocate",
            "out of memory",
            "oom",
            "std::bad_alloc",
            "cannot allocate",
        )
    )


def clear_whisper_cache() -> None:
    """Drop cached Whisper models to free RAM (STT OOM recovery)."""
    global _WHISPER_CACHE
    _WHISPER_CACHE.clear()
    try:
        import gc

        gc.collect()
    except Exception:
        pass


def _whisper_fallback_sizes(requested: str) -> list[str]:
    order = ["large-v3", "large-v2", "large", "medium", "small", "base", "tiny"]
    req = (requested or "tiny").strip().lower()
    if req not in order:
        return [req, "base", "tiny"]
    idx = order.index(req)
    # Prefer requested, then smaller only
    return order[idx:]


def load_whisper(app_dir: Path, size: str):
    key = size
    if key in _WHISPER_CACHE:
        return _WHISPER_CACHE[key]
    from faster_whisper import WhisperModel
    from engines.hardware_probe import probe_whisper_device

    device, compute_type = probe_whisper_device()
    root = str(hub_dir(app_dir))
    last_exc: BaseException | None = None

    # Missing requested size must NOT abort the dub — walk smaller sizes
    # that may already be on disk (CJK bump to «small» with only «tiny»
    # prepared was raising ModelNotPreparedError and killing STT).
    missing_requested = False
    for try_size in _whisper_fallback_sizes(size):
        if not verify_whisper(app_dir, try_size):
            if try_size == size:
                missing_requested = True
                if is_offline_only() or not downloads_permitted():
                    logger.warning(
                        "[ModelManager] Whisper %s not on disk (offline/no-download) "
                        "— trying smaller prepared sizes",
                        size,
                    )
                    continue
                assert_downloads_allowed("whisper load")
            else:
                continue
        attempts = [
            (device, compute_type),
            ("cpu", "int8"),
            ("cpu", "float32"),
        ]
        # Deduplicate identical attempts
        seen: set[tuple[str, str]] = set()
        for dev, ctype in attempts:
            pair = (dev, ctype)
            if pair in seen:
                continue
            seen.add(pair)
            try:
                model = WhisperModel(
                    try_size,
                    device=dev,
                    compute_type=ctype,
                    download_root=root,
                    cpu_threads=2,
                )
                _WHISPER_CACHE[try_size] = model
                if try_size != size:
                    # Alias so repeated requests for the missing size reuse
                    # the smaller model without re-walking the chain.
                    _WHISPER_CACHE[size] = model
                    logger.warning(
                        "[ModelManager] Whisper %s unavailable/OOM → using %s (%s/%s)",
                        size,
                        try_size,
                        dev,
                        ctype,
                    )
                touch_component(app_dir, "whisper", try_size, engine_hint="whisper")
                return model
            except Exception as exc:
                last_exc = exc
                if _is_whisper_oom(exc):
                    logger.warning(
                        "[ModelManager] Whisper load OOM size=%s device=%s: %s",
                        try_size,
                        dev,
                        exc,
                    )
                    clear_whisper_cache()
                    continue
                logger.debug(
                    "[ModelManager] Whisper load fail size=%s device=%s: %s",
                    try_size,
                    dev,
                    exc,
                )
                continue

    if missing_requested and (is_offline_only() or not downloads_permitted()):
        raise ModelNotPreparedError(
            f"Whisper {size} не установлен",
            component="whisper",
        )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Whisper {size}: failed to load")


def load_marian(app_dir: Path, src: str, tgt: str):
    key = f"{src}->{tgt}"
    if key in _MARIAN_CACHE:
        return _MARIAN_CACHE[key]
    name = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
    if not verify_hf_model(app_dir, name):
        if is_offline_only() or not downloads_permitted():
            raise ModelNotPreparedError(
                f"Переводчик {src}→{tgt} не установлен",
                component="mt",
                pair=f"{src}-{tgt}",
            )
        assert_downloads_allowed("marian load")
    from transformers import MarianMTModel, MarianTokenizer

    tok = MarianTokenizer.from_pretrained(name, **_local_kw())
    model = MarianMTModel.from_pretrained(name, **_local_kw())
    model.eval()
    _MARIAN_CACHE[key] = (tok, model, name)
    touch_component(app_dir, "mt", f"marian:{src}-{tgt}", engine_hint="marian", artifact_id=name)
    return _MARIAN_CACHE[key]


def load_nllb(app_dir: Path):
    global _NLLB_PIPELINE
    if _NLLB_PIPELINE is not None:
        return _NLLB_PIPELINE
    if not verify_hf_model(app_dir, NLLB_MODEL_ID):
        if is_offline_only() or not downloads_permitted():
            raise ModelNotPreparedError("NLLB не установлен", component="mt")
        assert_downloads_allowed("nllb load")
        _ensure_nllb(app_dir, "en", "ru")

    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline

    tok = AutoTokenizer.from_pretrained(NLLB_MODEL_ID, **_local_kw())
    model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL_ID, **_local_kw())
    _NLLB_PIPELINE = pipeline("translation", model=model, tokenizer=tok, max_length=512)
    touch_component(app_dir, "mt", "nllb", engine_hint="nllb", artifact_id=NLLB_MODEL_ID)
    return _NLLB_PIPELINE


def load_argos_translator(app_dir: Path, src: str, tgt: str):
    if not _argos_ready(src, tgt):
        if is_offline_only() or not downloads_permitted():
            raise ModelNotPreparedError(
                f"Argos {src}→{tgt} не установлен",
                component="mt",
                pair=f"{src}-{tgt}",
            )
        _ensure_argos(app_dir, src, tgt)
    import argostranslate.translate as argos

    installed = argos.get_installed_languages()
    fl = next((l for l in installed if l.code == src), None)
    tl = next((l for l in installed if l.code == tgt), None)
    if fl and tl:
        return fl.get_translation(tl)
    return None
