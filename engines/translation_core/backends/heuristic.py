"""Heuristic backend — deterministic localization variants without LLM."""

from __future__ import annotations

import re
from typing import Any

from engines.translation_core.backend import BackendCapabilities, TranslationBackend

_PAIR_MAP: dict[tuple[str, str], list[tuple[re.Pattern[str], str]]] = {
    ("en", "uk"): [
        (re.compile(r"\bHello\b", re.I), "Привіт"),
        (re.compile(r"\bHi\b", re.I), "Привіт"),
        (re.compile(r"\bHow are you\b", re.I), "Як справи"),
        (re.compile(r"\bThank you\b", re.I), "Дякую"),
        (re.compile(r"\bGoodbye\b", re.I), "До побачення"),
        (re.compile(r"\bYes\b", re.I), "Так"),
        (re.compile(r"\bNo\b", re.I), "Ні"),
        (re.compile(r"\bI am\b", re.I), "Я"),
        (re.compile(r"\bI'm\b", re.I), "Я"),
        (re.compile(r"\bhome\b", re.I), "додому"),
        (re.compile(r"\bdinner\b", re.I), "вечеря"),
        (re.compile(r"\bboy\b", re.I), "хлопець"),
        (re.compile(r"\bnamed\b", re.I), "на ім'я"),
        (re.compile(r"\bdrove\b", re.I), "їхав"),
        (re.compile(r"\bthrough\b", re.I), "через"),
        (re.compile(r"\bhis\b", re.I), "своє"),
        (re.compile(r"\bhometown\b", re.I), "рідне місто"),
        (re.compile(r"\bwas on his way\b", re.I), "їхав"),
        (re.compile(r"\bfor\b", re.I), "на"),
        (re.compile(r"\btoday\b", re.I), "сьогодні"),
    ],
    ("en", "ru"): [
        (re.compile(r"\bHello\b", re.I), "Привет"),
        (re.compile(r"\bHow are you\b", re.I), "Как дела"),
        (re.compile(r"\bThank you\b", re.I), "Спасибо"),
    ],
}


class HeuristicBackend(TranslationBackend):
    id = "heuristic"
    name = "Heuristic"
    version = "1"

    def initialize(self) -> None:
        return None

    def translate(
        self,
        text: str,
        *,
        src_lang: str,
        tgt_lang: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        src = (src_lang or "en").lower()[:2]
        tgt = (tgt_lang or "uk").lower()[:2]
        out = str(text or "")
        for pat, repl in _PAIR_MAP.get((src, tgt), []):
            out = pat.sub(repl, out)
        return out

    def health_check(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            offline=True,
            multi_variant=True,
            context_aware=True,
            languages=["en", "uk", "ru"],
        )
