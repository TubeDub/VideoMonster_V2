import logging
import asyncio
import os
import re
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def sanitize_tts_text(text: str) -> str:
    """Clean text before TTS: remove HTML, URLs, collapse repeated chars, strip technical noise."""
    if not text:
        return text
    # Remove HTML/XML tags
    text = re.sub(r"<[^>]{1,200}>", " ", text)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)
    # Collapse 3+ consecutive identical characters to 2 (prevents infinite "ІІІІІ...")
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    # Remove sequences of 5+ identical uppercase/digit groups with no vowels (technical IDs)
    text = re.sub(r"\b[A-Z0-9]{6,}\b", "", text)
    # Remove code-like symbols left after other cleanups
    text = re.sub(r"[<>{}|\\^~`]", " ", text)
    # Collapse multiple spaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

APP_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

DEFAULT_VOICE = "ru-RU-DmitryNeural"


TTS_RETRIES = 3          # п.45 — попытки перед ошибкой
TTS_RETRY_DELAY = 1.5   # секунд между попытками
TTS_SEGMENT_TIMEOUT = 120  # секунд на один сегмент (защита от зависания на 65%)
# Align parallel group timeout with single-segment timeout (was 30s → premature silence_gap).
PIPELINE_TTS_GROUP_TIMEOUT = float(
    (os.getenv("VM_PIPELINE_SEGMENT_TIMEOUT_SEC") or str(TTS_SEGMENT_TIMEOUT)).strip()
    or str(TTS_SEGMENT_TIMEOUT)
)


def _tts_rate() -> str:
    """Скорость Edge-TTS: VM_TTS_RATE, например -5% для более естественной речи."""
    return (os.getenv("VM_TTS_RATE") or "-5%").strip() or "-5%"


def _sanitize_pitch(pitch: str | None) -> str | None:
    """Edge TTS accepts integer Hz only, e.g. -2Hz, not -1.5Hz."""
    if not pitch:
        return None
    p = str(pitch).strip()
    if not p:
        return None
    import re

    m = re.match(r"^([+-]?)(\d+(?:\.\d+)?)\s*(Hz|hz)$", p)
    if not m:
        return p
    sign, num = m.group(1), m.group(2)
    val = int(round(float(num)))
    if sign == "-":
        val = -abs(val)
    elif sign == "+":
        val = abs(val)
    val = max(-50, min(50, val))
    if val == 0:
        return "+0Hz"
    return f"{'+' if val > 0 else ''}{val}Hz"


def _detect_lang_from_voice(voice: str) -> str:
    """Guess BCP-47 language tag from Edge TTS voice name (e.g. uk-UA-OstapNeural → uk)."""
    v = (voice or "").strip()
    m = re.match(r"^([a-z]{2})-([A-Z]{2})", v)
    if m:
        return m.group(1).lower()
    return "uk"


async def _generate_single(
    text: str,
    voice: str,
    path: str,
    rate: str | None = None,
    pitch: str | None = None,
    engine_id: str | None = None,
    emotion: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    """Generate one audio file via registry (default: edge-offline)."""
    t0 = time.perf_counter()
    ctx = context or {}
    # CRITICAL: edge_tts.Communicate calls xml.sax.saxutils.escape(text), which converts
    # "<speak...>" to "&lt;speak...&gt;", causing the TTS to literally speak the XML markup.
    # Strip any SSML wrapper here as the last line of defence before reaching edge_tts.
    text = sanitize_tts_text(text)
    if text.lstrip().startswith("<speak"):
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return

    # Stage 12/18/24: refuse non-target (uk) lang mix / wrong voice before Edge.
    _tgt = str(
        (context or {}).get("target_lang")
        or (context or {}).get("tts_language")
        or (context or {}).get("language")
        or _detect_lang_from_voice(voice)
        or ""
    )
    _tgt_n = str(_tgt or "").split("-")[0].lower()
    _ctx = context or {}
    _simple = bool(
        _ctx.get("simple_pipeline")
        or _ctx.get("happy_path")
        or _ctx.get("fail_loud_lang_lock")
    )
    try:
        from engines.tts_lang_lock import (
            assert_voice_matches_target,
            force_uk_tts_identity,
            is_latin_heavy,
            is_uk_tts_text_ok,
        )

        if _tgt_n == "uk":
            _ident = force_uk_tts_identity(
                target_lang="uk", engine_id=engine_id, voice=voice
            )
            voice = str(_ident.get("voice") or voice)
            if _ident.get("engine_id"):
                engine_id = _ident["engine_id"]
            _ctx["tts_language"] = "uk"
            _ctx["tts_voice"] = voice
            _ctx["language"] = "uk"
            heavy, lat_r = is_latin_heavy(text, threshold=0.30)
            if heavy:
                logger.warning(
                    "[TTS] latin_heavy_warning path=%s ratio=%.2f text=%.80s",
                    path,
                    lat_r,
                    text,
                )
                raise RuntimeError(
                    "PIPELINE_LANG_MIX: latin_letter_ratio>0.30 before TTS "
                    f"path={path} ratio={lat_r:.2f} text={text[:80]!r}"
                )

        # Stage 18/24: raise — no silent cs/sk/pl/ru fallback for uk.
        assert_voice_matches_target(
            voice, _tgt or _detect_lang_from_voice(voice) or "uk", raise_error=True
        )
        if _tgt_n == "uk" and not is_uk_tts_text_ok(text):
            logger.warning(
                "[TTS] reject_non_target lang_mix path=%s text=%.80s",
                path,
                text,
            )
            raise RuntimeError(
                "PIPELINE_LANG_MIX: cyrillic_ratio < 0.55 before Edge-TTS "
                f"path={path} text={text[:80]!r}"
            )
    except RuntimeError:
        raise
    except Exception as _ll:
        logger.debug("[TTS] lang lock skip: %s", _ll)

    text_for_cache = text
    # Add Unicode stress marks for natural intonation (UA/RU neural voices support U+0301)
    try:
        from engines.stress_marks import add_stress_marks
        lang = _detect_lang_from_voice(voice)
        text = add_stress_marks(text, lang=lang)
    except Exception:
        pass
    try:
        from engines.tts_backends import normalize_backend_name, resolve_voice_for_backend

        eid = normalize_backend_name(engine_id or "edge-offline")
        voice = resolve_voice_for_backend(voice, eid)
    except Exception:
        eid = (engine_id or "edge-offline").strip() or "edge-offline"
    if eid == "edge-offline":
        import edge_tts

        # Stage 24: never call Edge with tts_uk short ids (mykyta → Invalid voice).
        try:
            from engines.tts_backends import resolve_voice_for_backend as _rvb
            from engines.tts_lang_lock import force_uk_tts_identity

            if _tgt_n == "uk":
                _eident = force_uk_tts_identity(
                    target_lang="uk", engine_id="edge-offline", voice=voice
                )
                voice = str(_eident.get("voice") or "uk-UA-OstapNeural")
            else:
                voice = _rvb(voice, eid)
        except Exception:
            if not str(voice).startswith("uk-UA-") and _tgt_n == "uk":
                voice = "uk-UA-OstapNeural"

        last_err: Exception | None = None
        effective_rate = (rate or _tts_rate()).strip() or "-5%"
        effective_pitch = _sanitize_pitch(pitch)
        try:
            from engines.emotion_tagger import is_emotion_tts_enabled, tts_params_for_emotion
            from engines.core.feature_flags import is_enabled as _ff_enabled

            if emotion and (
                _ff_enabled("emotion_tts", developer_session=True) or is_emotion_tts_enabled()
            ):
                params = tts_params_for_emotion({"emotion": emotion})
                effective_rate = params.get("rate") or effective_rate
                effective_pitch = _sanitize_pitch(params.get("pitch")) or effective_pitch
        except Exception:
            pass

        # Stage 6: disk cache hit before Edge call (key = pre-stress text).
        try:
            from engines.tts_cache import (
                default_cache_dir,
                lookup_tts_cache,
                materialize_cached,
                store_tts_cache,
            )

            cached = lookup_tts_cache(
                text_for_cache,
                voice,
                rate=effective_rate,
                pitch=str(effective_pitch or ""),
                engine_id=eid,
                cache_dir=default_cache_dir(),
                ext=Path(path).suffix or ".mp3",
                lang=_tgt_n,
            )
            if cached is not None and materialize_cached(cached, path):
                logger.info(
                    "[TTS] tts_cache_hit path=%s segment_id=%s",
                    path,
                    ctx.get("segment_id", "-"),
                )
                return
        except Exception as _cache_exc:
            logger.debug("[TTS] cache lookup skipped: %s", _cache_exc)

        kwargs: dict = {"text": text, "voice": voice, "rate": effective_rate}
        if effective_pitch:
            kwargs["pitch"] = effective_pitch
        for attempt in range(TTS_RETRIES):
            try:
                communicate = edge_tts.Communicate(**kwargs)
                await asyncio.wait_for(
                    communicate.save(path),
                    timeout=TTS_SEGMENT_TIMEOUT,
                )
                if not Path(path).exists() or Path(path).stat().st_size == 0:
                    raise RuntimeError(f"TTS produced empty file: {path}")
                try:
                    store_tts_cache(
                        path,
                        text_for_cache,
                        voice,
                        rate=effective_rate,
                        pitch=str(effective_pitch or ""),
                        engine_id=eid,
                        cache_dir=default_cache_dir(),
                        lang=_tgt_n,
                    )
                except Exception:
                    pass
                return
            except asyncio.TimeoutError:
                last_err = TimeoutError(
                    f"TTS timeout ({TTS_SEGMENT_TIMEOUT}s) for segment: {text[:80]!r}..."
                )
                logger.error(
                    "[TTS] timeout path=%s voice=%s segment_id=%s attempt=%d/%d",
                    path,
                    voice,
                    ctx.get("segment_id", "-"),
                    attempt + 1,
                    TTS_RETRIES,
                )
            except Exception as e:
                last_err = e
                logger.warning(
                    "[TTS] attempt %d/%d path=%s voice=%s segment_id=%s failed: %s",
                    attempt + 1,
                    TTS_RETRIES,
                    path,
                    voice,
                    ctx.get("segment_id", "-"),
                    e,
                )
            if attempt < TTS_RETRIES - 1:
                await asyncio.sleep(TTS_RETRY_DELAY)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        tb = traceback.format_exc()
        logger.error(
            "[TTS] FAILED path=%s voice=%s segment_id=%s duration_ms=%.1f error=%s\n%s",
            path,
            voice,
            ctx.get("segment_id", "-"),
            duration_ms,
            last_err,
            tb,
        )
        from engines.dubbing_engine.tts_failure_diag import (
            VoiceGenerationError,
            build_failure_report,
            log_tts_failure,
        )

        report = build_failure_report(
            last_err or RuntimeError("TTS failed without error detail"),
            segment_id=str(ctx.get("segment_id") or ""),
            segment_index=int(ctx.get("segment_index", 0)),
            current=int(ctx.get("current", ctx.get("segment_index", 0) + 1)),
            total=int(ctx.get("total", 1)),
            original_text=str(ctx.get("original_text") or text),
            tts_text=str(ctx.get("tts_text") or text),
            voice=voice,
            language=str(ctx.get("language") or _detect_lang_from_voice(voice)),
            tts_file_path=path,
            duration_ms=duration_ms,
            task_id=str(ctx.get("task_id") or ""),
            engine_id=eid,
            pipeline_state=str(ctx.get("pipeline_state") or "PARTIAL"),
        )
        log_tts_failure(report)
        raise VoiceGenerationError(str(last_err), report=report, cause=last_err) from last_err

    from engines.tts_backends import resolve_mykyta_controls, synthesize_with_backend

    loop = asyncio.get_running_loop()
    mykyta = None
    if eid in ("tts_uk",):
        mykyta = resolve_mykyta_controls(
            {
                "rate": (context or {}).get("tts_rate", rate),
                "pitch": (context or {}).get("tts_pitch", pitch),
                "volume": (context or {}).get("tts_volume"),
                "length_scale": (context or {}).get("tts_length_scale"),
            }
        )

    def _sync() -> None:
        t_sync = time.perf_counter()
        _tgt_pass = str(
            (context or {}).get("target_lang")
            or (context or {}).get("tts_language")
            or (context or {}).get("language")
            or ""
        )
        result = synthesize_with_backend(
            text,
            voice,
            path,
            engine_id=eid,
            rate=rate if mykyta is None else str(mykyta["rate"]),
            pitch=pitch if mykyta is None else str(mykyta["pitch"]),
            volume=None if mykyta is None else mykyta["volume"],
            length_scale=None if mykyta is None else mykyta["length_scale"],
            mykyta_controls=mykyta,
            target_lang=_tgt_pass or None,
        )
        if not result.ok:
            duration_ms = (time.perf_counter() - t_sync) * 1000.0
            err = RuntimeError(result.error or "TTS failed")
            logger.error(
                "[TTS] engine=%s path=%s segment_id=%s failed: %s duration_ms=%.1f",
                eid,
                path,
                ctx.get("segment_id", "-"),
                result.error,
                duration_ms,
            )
            raise err

    try:
        await loop.run_in_executor(None, _sync)
    except Exception as last_err:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        from engines.dubbing_engine.tts_failure_diag import (
            VoiceGenerationError,
            build_failure_report,
            log_tts_failure,
        )

        report = build_failure_report(
            last_err,
            segment_id=str(ctx.get("segment_id") or ""),
            segment_index=int(ctx.get("segment_index", 0)),
            current=int(ctx.get("current", ctx.get("segment_index", 0) + 1)),
            total=int(ctx.get("total", 1)),
            original_text=str(ctx.get("original_text") or text),
            tts_text=str(ctx.get("tts_text") or text),
            voice=voice,
            language=str(ctx.get("language") or _detect_lang_from_voice(voice)),
            tts_file_path=path,
            duration_ms=duration_ms,
            task_id=str(ctx.get("task_id") or ""),
            engine_id=eid,
            pipeline_state=str(ctx.get("pipeline_state") or "PARTIAL"),
        )
        log_tts_failure(report)
        raise VoiceGenerationError(str(last_err), report=report, cause=last_err) from last_err


async def _generate_batch(
    segments: list,
    voice: str,
    output_dir: Path,
    task_id: str,
    rate: str | None = None,
    pitch: str | None = None,
    engine_id: str | None = None,
    emotions: list[str | None] | None = None,
    contexts: list[dict[str, Any] | None] | None = None,
) -> list:
    """Генерирует несколько аудиофайлов (по сегментам) с уникальными именами."""
    from engines.pipeline_integrity.audio_identity import allocate_tts_path

    files = []
    for i, seg in enumerate(segments):
        if not seg.strip():
            continue
        ctx = contexts[i] if contexts and i < len(contexts) else None
        ctx = dict(ctx or {})
        segment_uuid = str(
            ctx.get("segment_id") or ctx.get("segment_uuid") or ""
        ).strip() or f"{task_id}_{i:04d}_{uuid.uuid4().hex[:8]}"
        path_obj = allocate_tts_path(
            output_dir,
            segment_uuid=segment_uuid,
            run_id=str(task_id or ""),
            ext=".mp3",
            purpose="tts",
        )
        filename = path_obj.name
        path = str(path_obj)
        emo = emotions[i] if emotions and i < len(emotions) else None
        ctx.setdefault("segment_id", segment_uuid)
        ctx.setdefault("segment_uuid", segment_uuid)
        ctx.setdefault("segment_index", i)
        ctx.setdefault("tts_file_path", path)
        await _generate_single(
            seg,
            voice,
            path,
            rate=rate,
            pitch=pitch,
            engine_id=engine_id,
            emotion=emo,
            context=ctx,
        )
        files.append(filename)
    return files


def generate_audio(
    text: str,
    voice: str = DEFAULT_VOICE,
    segments: list = None,
    rate: str | None = None,
    pitch: str | None = None,
    engine_id: str | None = None,
    emotion: str | None = None,
    output_dir: Path | None = None,
    task_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> list:
    """
    Генерирует аудио из текста или сегментов.
    Возвращает список имён файлов в output_dir (по умолчанию OUTPUT_DIR).
    """
    out_dir = output_dir or OUTPUT_DIR
    batch_id = task_id or uuid.uuid4().hex[:8]
    segs = segments if segments else [text]
    segs = [sanitize_tts_text(s) for s in segs]
    segs = [s for s in segs if s.strip()]

    if not segs:
        return []

    files = asyncio.run(
        _generate_batch(
            segs,
            voice,
            out_dir,
            batch_id,
            rate=rate,
            pitch=pitch,
            engine_id=engine_id,
            emotions=[emotion] * len(segs) if emotion else None,
            contexts=[context] * len(segs) if context else None,
        )
    )
    return files


def _tts_max_concurrent() -> int:
    """Prefer EDGE_TTS_CONCURRENCY (Stage 6), else VM_TTS_PARALLEL; default 6, cap 8."""
    try:
        from engines.tts_parallel import resolve_edge_tts_concurrency

        return resolve_edge_tts_concurrency(None)
    except Exception:
        raw = (os.getenv("EDGE_TTS_CONCURRENCY") or os.getenv("VM_TTS_PARALLEL") or "6").strip()
        try:
            return max(1, min(8, int(raw)))
        except ValueError:
            return 6


async def _generate_groups_parallel_async(
    items: list[dict[str, Any]],
    voice: str,
    output_dir: Path,
    *,
    max_concurrent: int = 3,
    rate: str | None = None,
    pitch: str | None = None,
    engine_id: str | None = None,
    on_group_done: Any = None,
) -> list[dict[str, Any]]:
    """Parallel TTS for dub groups — same voice/rate/pitch, no quality change."""
    if not items:
        return []

    batch_id = uuid.uuid4().hex[:8]
    sem = asyncio.Semaphore(max(1, max_concurrent))
    total = len(items)
    done_count = 0

    async def _one(item: dict[str, Any]) -> dict[str, Any]:
        nonlocal done_count
        text = str(item.get("text") or "").strip()
        g_idx = int(item.get("g_idx", 0))
        ctx = dict(item.get("tts_context") or {})
        if not text:
            done_count += 1
            if on_group_done:
                on_group_done(g_idx, total, done_count)
            return {**item, "file": None}
        async with sem:
            from engines.pipeline_integrity.audio_identity import allocate_tts_path

            segment_uuid = str(
                ctx.get("segment_id") or ctx.get("segment_uuid") or ""
            ).strip() or f"g{g_idx:04d}_{uuid.uuid4().hex[:8]}"
            path_obj = allocate_tts_path(
                output_dir,
                segment_uuid=segment_uuid,
                run_id=batch_id,
                ext=".mp3",
                purpose="tts",
            )
            filename = path_obj.name
            path = str(path_obj)
            item_rate = item.get("rate") or rate
            item_pitch = item.get("pitch") or pitch
            item_voice = str(item.get("voice") or voice or "").strip() or voice
            ctx.setdefault("segment_id", segment_uuid)
            ctx.setdefault("segment_uuid", segment_uuid)
            ctx.setdefault("tts_file_path", path)
            try:
                await asyncio.wait_for(
                    _generate_single(
                        text,
                        item_voice,
                        path,
                        rate=item_rate,
                        pitch=item_pitch,
                        engine_id=engine_id,
                        context=ctx,
                    ),
                    timeout=PIPELINE_TTS_GROUP_TIMEOUT,
                )
                done_count += 1
                if on_group_done:
                    on_group_done(g_idx, total, done_count)
                return {**item, "file": filename, "tts_ok": True}
            except asyncio.TimeoutError:
                logger.warning(
                    "[TTS] pipeline group timeout after %.0fs g_idx=%d segment_id=%s",
                    PIPELINE_TTS_GROUP_TIMEOUT,
                    g_idx,
                    ctx.get("segment_id", "-"),
                )
                done_count += 1
                if on_group_done:
                    on_group_done(g_idx, total, done_count)
                return {
                    **item,
                    "file": None,
                    "tts_ok": False,
                    "tts_failure": {
                        "error_message": f"pipeline_timeout_{PIPELINE_TTS_GROUP_TIMEOUT}s",
                        "segment_id": str(ctx.get("segment_id") or ""),
                        "segment_index": int(ctx.get("segment_index", 0)),
                        "stage": "TTS",
                    },
                }
            except Exception as exc:
                from engines.dubbing_engine.tts_failure_diag import VoiceGenerationError

                report = None
                if isinstance(exc, VoiceGenerationError) and exc.report:
                    report = exc.report.to_dict()
                done_count += 1
                if on_group_done:
                    on_group_done(g_idx, total, done_count)
                return {
                    **item,
                    "file": None,
                    "tts_ok": False,
                    "tts_failure": report or {"error_message": str(exc)},
                }

    results = await asyncio.gather(*[_one(it) for it in items])
    return sorted(results, key=lambda r: int(r.get("g_idx", 0)))


def generate_tts_groups_parallel(
    items: list[dict[str, Any]],
    voice: str,
    *,
    max_concurrent: int | None = None,
    rate: str | None = None,
    pitch: str | None = None,
    engine_id: str | None = None,
    on_group_done: Any = None,
    output_dir: Path | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Generate TTS for multiple dub groups concurrently.
    Each item: {g_idx, indices, text, timing?}.
    Returns same items with 'file' set (or None).
    """
    out_dir = output_dir or OUTPUT_DIR
    workers = max_concurrent if max_concurrent is not None else _tts_max_concurrent()
    if len(items) <= 1:
        workers = 1
    return asyncio.run(
        _generate_groups_parallel_async(
            items,
            voice,
            out_dir,
            max_concurrent=workers,
            rate=rate,
            pitch=pitch,
            engine_id=engine_id,
            on_group_done=on_group_done,
        )
    )


def cleanup_old_files(max_age_seconds: int = 3600) -> None:
    """Удаляет MP3-файлы старше max_age_seconds."""
    now = time.time()
    for f in OUTPUT_DIR.glob("*.mp3"):
        try:
            if now - f.stat().st_mtime > max_age_seconds:
                f.unlink()
        except OSError as e:
            logger.warning("cleanup_old_files: %s", e)


def get_output_path(filename: str) -> Path:
    """Возвращает Path к файлу, если он существует и лежит под OUTPUT_DIR."""
    from engines.path_safety import is_under_root

    safe = Path(filename).name
    if not safe or safe != Path(filename).name:
        return None
    try:
        path = (OUTPUT_DIR / safe).resolve()
    except OSError:
        return None
    if not is_under_root(path, OUTPUT_DIR):
        return None
    return path if path.exists() else None
