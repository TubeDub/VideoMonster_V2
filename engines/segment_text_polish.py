"""Light entity/grammar touch-up before TTS — does not replace Semantic v4."""

from __future__ import annotations

from typing import Any


def polish_segments_for_tts(
    segments_data: list[dict[str, Any]],
    source_segments: list[str],
    *,
    target_lang: str,
) -> int:
    """Apply entity dictionary + minimal grammar on post-semantic text fields."""
    if not segments_data:
        return 0

    from engines.ai_core.entity_dictionary import EntityDictionary
    from engines.ai_core.grammar_agent.rule_engine import fix_grammar

    text_keys = (
        "grammar_text",
        "timing_text",
        "semantic_text",
        "text",
        "plain_text",
        "text_for_tts",
        "tts_text",
    )

    entity_dict = EntityDictionary.from_segments(
        [
            {
                **s,
                "text": str(
                    source_segments[i] if i < len(source_segments) else s.get("text") or ""
                ),
            }
            for i, s in enumerate(segments_data)
        ],
        target_lang=target_lang,
    )

    changed = 0
    for i, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        source = str(source_segments[i] if i < len(source_segments) else seg.get("text") or "")
        base = str(
            seg.get("semantic_text")
            or seg.get("grammar_text")
            or seg.get("timing_text")
            or seg.get("translated_text")
            or seg.get("text")
            or ""
        ).strip()
        if not base:
            continue

        polished = entity_dict.apply(base, source=source)
        if target_lang == "uk":
            polished = fix_grammar(polished, target_lang)

        if polished != base:
            changed += 1

        for key in text_keys:
            if key in seg or key in ("text", "plain_text", "text_for_tts"):
                seg[key] = polished

    return changed
