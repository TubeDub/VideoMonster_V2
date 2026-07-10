"""Live MT — Translation Manager + optional light polish."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def translate_phrase_live(
    text: str,
    *,
    src_lang: str,
    tgt_lang: str,
    app_dir: Path,
    context: list[str] | None = None,
    use_naturalizer: bool = True,
    use_enterprise: bool = False,
) -> dict[str, Any]:
    """Fast phrase translation for live pipeline."""
    phrase = str(text or "").strip()
    if not phrase:
        return {"text": "", "engine": "", "router_reason": "", "elapsed_ms": 0.0}

    src = (src_lang or "en").split("-")[0].lower()
    tgt = (tgt_lang or "ru").split("-")[0].lower()
    if src == tgt:
        return {
            "text": phrase,
            "engine": "passthrough",
            "router_reason": "same_language",
            "elapsed_ms": 0.0,
        }

    t0 = time.perf_counter()
    engine = ""
    router_reason = ""
    out = phrase

    try:
        from engines.translation_manager import translate_with_manager

        ctx = " ".join((context or [])[-2:]).strip() or None
        result_text, meta = translate_with_manager(
            phrase,
            src,
            tgt,
            app_dir=app_dir,
            context=ctx,
            source_original=phrase,
        )
        out = str(result_text or phrase).strip()
        engine = str(meta.get("engine") or "")
        router_reason = str(meta.get("router_reason") or meta.get("route_label") or "")
    except Exception:
        from engines.translation import translate_text

        out = translate_text(phrase, src, tgt)
        engine = "translate_text"
        router_reason = "fallback_translate_text"

    if use_enterprise:
        try:
            from engines.broadcast.integration import translate_with_broadcast

            polished, bmeta = translate_with_broadcast(
                phrase, src, tgt, app_dir=app_dir, source_original=phrase
            )
            if polished:
                out = polished
                router_reason = f"{router_reason}+broadcast"
                engine = str(bmeta.get("engine") or engine)
        except Exception:
            pass

    if use_naturalizer and out:
        try:
            from engines.translation_naturalizer import apply_style_polish

            out = apply_style_polish(
                out, tgt, source=phrase, app_dir=app_dir
            )
            router_reason = f"{router_reason}+style_polish"
        except Exception:
            pass

    elapsed = (time.perf_counter() - t0) * 1000.0
    return {
        "text": out,
        "engine": engine,
        "router_reason": router_reason,
        "elapsed_ms": round(elapsed, 2),
    }
