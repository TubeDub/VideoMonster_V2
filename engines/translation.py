import logging
import re

from engines.mt.argos_engine import translate_argos
from engines.mt.deep_engine import DeepTranslatorEngine
from engines.mt.lang_codes import deep_lang, normalize_lang as _normalize_code

logger = logging.getLogger("tubedub.engines.translation")

# Backward-compatible aliases
LANG_ALIASES = {
    "eng": "en",
    "english": "en",
    "rus": "ru",
    "ukr": "uk",
    "ger": "de",
    "deu": "de",
    "jpn": "ja",
    "zh-cn": "zh",
    "zh_cn": "zh",
}
DEEP_LANG_MAP = {"zh": "zh-CN", "zh-cn": "zh-CN"}


def _deep_lang(code: str) -> str:
    return deep_lang(code)


def _translate_argos(text: str, src: str, tgt: str) -> str | None:
    return translate_argos(text, src, tgt)


def _translate_deep(text: str, src: str, tgt: str) -> str:
    r = DeepTranslatorEngine().translate(text, src, tgt)
    if not r.text:
        raise RuntimeError(
            r.error or "Не удалось перевести текст. Проверьте интернет-соединение."
        )
    return r.text


def translate_text(text: str, src_lang: str, tgt_lang: str) -> str:
    translated, _meta = translate_text_traced(text, src_lang, tgt_lang)
    return translated


def translate_text_traced(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    context: str | None = None,
    next_context: str | None = None,
    app_dir=None,
    segment_index: int = -1,
    source_original: str | None = None,
) -> tuple[str, dict]:
    from pathlib import Path

    from engines.mt.stable_translate import translate_direct_marian, use_stable_mt

    base_dir = Path(app_dir) if app_dir else Path(__file__).resolve().parent.parent

    from engines.broadcast.config import use_broadcast_pipeline
    from engines.enterprise_translation.config import use_enterprise_translation
    from engines.str.config import use_str
    from engines.translation_manager import use_translation_manager

    if use_broadcast_pipeline():
        from engines.broadcast.integration import translate_with_broadcast

        translated, meta = translate_with_broadcast(
            text,
            src_lang,
            tgt_lang,
            app_dir=base_dir,
            context=context,
            next_context=next_context,
            segment_index=segment_index,
            source_original=source_original,
        )
    elif use_enterprise_translation():
        from engines.enterprise_translation.integration import translate_with_enterprise

        translated, meta = translate_with_enterprise(
            text,
            src_lang,
            tgt_lang,
            app_dir=base_dir,
            context=context,
            next_context=next_context,
            segment_index=segment_index,
        )
    elif use_str():
        from engines.str.router import translate_with_str

        translated, meta = translate_with_str(
            text,
            src_lang,
            tgt_lang,
            app_dir=base_dir,
            context=context,
            next_context=next_context,
            segment_index=segment_index,
        )
    elif use_translation_manager():
        from engines.translation_manager import translate_with_manager

        translated, meta = translate_with_manager(
            text,
            src_lang,
            tgt_lang,
            app_dir=base_dir,
            context=context,
            next_context=next_context,
            segment_index=segment_index,
            source_original=source_original,
        )
    elif use_stable_mt():
        translated, meta = translate_direct_marian(
            text,
            src_lang,
            tgt_lang,
            app_dir=base_dir,
            segment_index=segment_index,
        )
    else:
        from engines.translation_router import translate_with_router

        translated, meta = translate_with_router(
            text,
            src_lang,
            tgt_lang,
            app_dir=base_dir,
            context=context,
            next_context=next_context,
            segment_index=segment_index,
        )
    meta.setdefault("context_used", bool(context and str(context).strip()))
    meta.setdefault("next_context_used", bool(next_context and str(next_context).strip()))
    return translated, meta


def translate_segments(
    segments: list[str],
    src_lang: str,
    tgt_lang: str,
) -> list[str]:
    if not segments:
        return []

    src = _normalize_code(src_lang or "en")
    tgt = _normalize_code(tgt_lang or "ru")

    if src == tgt:
        return [str(s).strip() for s in segments]

    translated: list[str] = []
    for idx, raw in enumerate(segments):
        seg = str(raw or "").strip()
        if not seg:
            translated.append("")
            continue
        try:
            out = translate_text(seg, src, tgt)
            translated.append((out or seg).strip())
        except Exception as e:
            logger.warning(
                "[Translation] Segment %d failed (%s->%s): %s",
                idx,
                src,
                tgt,
                e,
            )
            translated.append(seg)

    logger.info(
        "[Translation] Segments translated: %d (src=%s tgt=%s)",
        len(translated),
        src,
        tgt,
    )
    return translated
