"""Human-readable component labels — never show engine names to users."""

from __future__ import annotations

COMPONENT_LABELS: dict[str, dict[str, str]] = {
    "whisper": {"ru": "Распознавание речи", "en": "Speech recognition", "uk": "Розпізнавання мовлення"},
    "mt": {"ru": "Переводчик", "en": "Translator", "uk": "Перекладач"},
    "tts": {"ru": "Озвучка", "en": "Voice", "uk": "Озвучення"},
    "ocr": {"ru": "Распознавание текста", "en": "Text recognition", "uk": "Розпізнавання тексту"},
    "naturalizer": {"ru": "Улучшение перевода", "en": "Translation polish", "uk": "Покращення перекладу"},
    "semantic": {"ru": "Смысловая адаптация", "en": "Semantic adaptation", "uk": "Смислова адаптація"},
    "voice_fx": {"ru": "Обработка голоса", "en": "Voice FX", "uk": "Обробка голосу"},
    "router": {"ru": "Маршрут перевода", "en": "Translation routing", "uk": "Маршрут перекладу"},
    "llm": {"ru": "Языковая модель", "en": "Language model", "uk": "Мовна модель"},
}


def label(component_id: str, ui_lang: str = "ru") -> str:
    lang = (ui_lang or "ru").split("-")[0].lower()
    entry = COMPONENT_LABELS.get(component_id, {})
    return entry.get(lang) or entry.get("ru") or component_id
