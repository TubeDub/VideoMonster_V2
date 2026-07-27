"""LLM direct retranslation for meaning-collapse recovery (CJK → UK/RU).

Argos/Marian often invent fluent garbage for zh→uk. Naturalizer polish cannot
recover lost meaning — only a fresh translation from source can.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any

logger = logging.getLogger("tubedub.mt.llm_retranslate")

_CJK_SRC = frozenset({"zh", "zh-cn", "zh-tw", "ja", "ko", "yue"})
_CYR_TGT = frozenset({"uk", "ru", "be"})


def should_llm_retranslate(*, src_lang: str | None, tgt_lang: str | None) -> bool:
    src = str(src_lang or "").split("-")[0].lower()
    tgt = str(tgt_lang or "").split("-")[0].lower()
    return src in _CJK_SRC and tgt in _CYR_TGT


def llm_direct_translate(
    text: str,
    *,
    src_lang: str = "zh",
    tgt_lang: str = "uk",
    task_id: str = "",
    segment_idx: int | None = None,
    max_tokens: int = 700,
) -> str | None:
    """Translate source → target via LLM. Returns None on failure."""
    src = str(text or "").strip()
    if not src:
        return None

    # Prefer curated offline gloss for known drama lines (no network / no waffle)
    try:
        from engines.mt.zh_drama_gloss import try_offline_gloss_rescue

        gloss = try_offline_gloss_rescue(
            src, "", src_lang=src_lang, tgt_lang=tgt_lang
        )
        if gloss and str(gloss.get("text") or "").strip():
            return str(gloss["text"]).strip()
    except Exception:
        pass

    lang_name = {
        "uk": "українську",
        "ru": "русский",
        "be": "беларускую",
    }.get(str(tgt_lang or "uk").split("-")[0].lower(), "українську")

    glossary = _cue_glossary(src, tgt_lang=tgt_lang)
    system = (
        f"Ти професійний дубляж-перекладач китайських драм на {lang_name}. "
        "Перекладай сенс точно й природно. ASR може бути з помилками — "
        "відновлюй очевидний сенс за контекстом драми. "
        "Не вигадуй нових сюжетів. "
        "Ніколи не залишай ієрогліфи / китайські символи в перекладі. "
        "«怀孕» = вагітна (НЕ «народила»). "
        "Поверни лише текст перекладу без пояснень і лапок."
    )
    if glossary:
        system += " Обов'язкові відповідники, якщо є в оригіналі: " + "; ".join(glossary) + "."

    prompt = f"Переклади на {lang_name}:\n{src}"

    out = _chat_gateway(
        prompt,
        system=system,
        task_id=task_id,
        segment_idx=segment_idx,
        max_tokens=max_tokens,
    )
    if not out:
        out = _chat_ollama_direct(prompt, system=system, max_tokens=max_tokens)

    cleaned = _clean_llm_output(out)
    if not cleaned:
        return None

    try:
        from engines.mt.cross_script_guard import (
            meaning_collapse,
            source_script_leak,
            strip_source_script_chars,
        )

        # Strip residual CJK/Arabic tails before accepting (TPS dump mixed UK+zh)
        stripped = strip_source_script_chars(
            cleaned, source_lang=src_lang, source=src
        )
        if stripped and stripped != cleaned:
            cleaned = stripped
        if source_script_leak(
            src, cleaned, source_lang=src_lang, target_lang=tgt_lang
        ):
            return None
        collapse = meaning_collapse(
            src, cleaned, source_lang=src_lang, target_lang=tgt_lang
        )
        if collapse and not _collapse_acceptable(collapse):
            logger.info(
                "llm_direct_translate: still meaning_collapse after LLM reasons=%s",
                collapse.get("reasons"),
            )
            return None
    except Exception:
        pass

    return cleaned


def _cue_glossary(src: str, *, tgt_lang: str) -> list[str]:
    """Force critical cue glosses into the prompt when source contains them."""
    tgt = str(tgt_lang or "uk").split("-")[0].lower()
    uk = tgt in ("uk", "be")
    pairs = [
        (("怀孕", "身孕", "孕妇"), "怀孕 → вагітна/вагітність" if uk else "怀孕 → беременна"),
        (("绑架", "绑匪", "绑费", "绑子"), "绑架/绑匪 → викрадення/викрадач" if uk else "绑架 → похищение"),
        (("孩子", "小孩", "宝宝"), "孩子 → дитина" if uk else "孩子 → ребёнок"),
        (("意外", "事故"), "意外 → випадок" if uk else "意外 → случай"),
        (("八代", "单传", "子嗣"), "八代/单传/子嗣 → вісім поколінь / єдиний спадкоємець" if uk else "八代 → восемь поколений"),
        (("有后",), "有后 → є спадкоємець" if uk else "有后 → есть наследник"),
        (("男", "女"), "男/女 → хлопчик/дівчинка (стать дитини)" if uk else "男/女 → мальчик/девочка"),
    ]
    out: list[str] = []
    for cues, gloss in pairs:
        if any(c in src for c in cues):
            out.append(gloss)
    return out


def _collapse_acceptable(collapse: dict[str, Any]) -> bool:
    """Allow mild residual collapse after LLM when most cues landed."""
    reasons = list(collapse.get("reasons") or [])
    # Never accept residual CJK / phrase loops / pregnancy flip from LLM
    hard = (
        "phrase_loop",
        "pregnancy_to_birth_flip",
        "meta_waffle",
        "meta_waffle_hallucination",
        "leak_plus_waffle",
    )
    if any(r in hard or r.startswith("critical_cue_lost:") for r in reasons):
        return False
    missing = list(collapse.get("missing_gloss") or [])
    hits = list(collapse.get("source_hits") or [])
    if not hits:
        return True
    covered = max(0, len(hits) - len(missing))
    # Accept if ≥ half cues covered and at most one key cue still missing
    return covered >= max(1, (len(hits) + 1) // 2) and len(missing) <= 1


def _chat_gateway(
    prompt: str,
    *,
    system: str,
    task_id: str,
    segment_idx: int | None,
    max_tokens: int,
) -> str | None:
    try:
        from engines.ai_core.llm_gateway import chat, is_available
        from engines.llm_callable import is_llm_callable, refresh_endpoint_models

        refresh_endpoint_models(force=False)
        if not (is_available() and is_llm_callable(quick=True)):
            return None
        return chat(
            prompt,
            task_id=task_id or "mt_collapse_retry",
            segment_idx=segment_idx,
            system=system,
            max_tokens=max_tokens,
            temperature=0.15,
            timeout=300.0,
            # Recovery must not die on segment budget / cold-start circuit
            count_budget=False,
        )
    except Exception as exc:
        logger.warning("llm_direct_translate gateway failed: %s", exc)
        return None


def _chat_ollama_direct(
    prompt: str,
    *,
    system: str,
    max_tokens: int,
    timeout: float = 300.0,
) -> str | None:
    """Bypass gateway — cold-start resilient path for local Ollama."""
    import os

    model = (os.getenv("VM_TRANSLATE_MODEL") or "qwen2.5:7b").strip()
    host = (os.getenv("VM_OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "options": {"temperature": 0.15, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str((data.get("message") or {}).get("content") or "").strip()
    except Exception as exc:
        logger.warning("llm_direct_translate ollama direct failed: %s", exc)
        return None


def _clean_llm_output(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:переклад|translation)\s*[:：]\s*", "", text, flags=re.I)
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("«") and text.endswith("»")
    ):
        text = text[1:-1].strip()
    return text.strip()
