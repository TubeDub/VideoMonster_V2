"""P31/P33 + Part 3 — Native Sentence Translation via Translation Core."""

from __future__ import annotations

import logging
from typing import Any, Callable

from engines.semantic_v3.types import SemanticSentence
from engines.semantic_v3.quality import validate_all

logger = logging.getLogger("tubedub.semantic_v3.native_translate")


def translate_sentences_native(
    sentences: list[SemanticSentence],
    *,
    src_lang: str,
    tgt_lang: str,
    translate_fn: Callable[[str, str, str], str] | None = None,
    app_dir: Any = None,
    backend_id: str | None = None,
    lock: bool = True,
) -> list[SemanticSentence]:
    """
    Translation Engine input = SemanticSentence only.
    Forbidden: Whisper Segment / Chunk / Window / Buffer.
    Delegates to Translation Core (Part 3) unless a custom translate_fn is provided.
    """
    if translate_fn is not None:
        return _legacy_single_pass(
            sentences,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            translate_fn=translate_fn,
            lock=lock,
        )

    from engines.translation_core import translate_sentences

    # Prefer heuristic offline unless backend forced — mt_bridge may hit network
    bid = backend_id
    if bid is None:
        import os

        bid = os.environ.get("VM_TRANSLATION_BACKEND", "").strip() or None

    result = translate_sentences(
        sentences,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        backend_id=bid,
        lock=lock,
    )
    qa = validate_all(sentences)
    if not qa.get("ok"):
        logger.warning("native translate validation failed=%s", qa.get("failed"))
    logger.info(
        "TranslationCore: backend=%s locked=%s reports=%d",
        result.backend_id,
        result.locked,
        len(result.reports),
    )
    return sentences


def _legacy_single_pass(
    sentences: list[SemanticSentence],
    *,
    src_lang: str,
    tgt_lang: str,
    translate_fn: Callable[[str, str, str], str],
    lock: bool,
) -> list[SemanticSentence]:
    from engines.semantic_v3.semantic_lock import apply_locked_translation
    from engines.translation_core.engine import assert_sentence_only

    for s in sentences:
        assert_sentence_only(s)
        src = (s.text or "").strip()
        if not src:
            continue
        try:
            out = translate_fn(src, src_lang, tgt_lang)
        except Exception as exc:
            logger.warning("native translate failed %s: %s", s.sentence_uuid, exc)
            out = src
        if lock:
            apply_locked_translation(s, out)
        else:
            s.translated_text = " ".join(str(out or "").split())
    return sentences


def assert_not_whisper_unit(unit: Any) -> None:  # noqa: ANN401
    from engines.translation_core.engine import assert_sentence_only

    assert_sentence_only(unit)
