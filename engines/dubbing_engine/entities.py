"""
Stage 1 — Entity Context.

Recognises named entities in the source text (person names, brands, car models,
organisations, geographic names) and verifies they survive translation intact.
No external NLP model required: uses pattern matching + a curated lookup table.
"""

from __future__ import annotations

import re
from typing import Any

from engines.dubbing_engine.types import EntityInfo

# ── Known entity labels ────────────────────────────────────────────────────────
# car_brands: should NEVER be translated
_CAR_BRANDS = frozenset({
    "Fiat", "FIAT", "Toyota", "Honda", "BMW", "Mercedes", "Audi", "Ford",
    "Chevrolet", "Volkswagen", "VW", "Renault", "Peugeot", "Hyundai", "Kia",
    "Tesla", "Ferrari", "Lamborghini", "Porsche", "Lexus", "Subaru", "Mazda",
    "Volvo", "Nissan", "Mitsubishi", "Citroën", "Citroen", "Dodge", "Jeep",
    "Land Rover", "Range Rover", "Bentley", "Rolls-Royce", "Maserati",
    "Alfa Romeo", "Seat", "Skoda", "Opel", "Buick", "Cadillac", "Lincoln",
})

# tech/consumer brands: should stay as-is or transliterate, not translate
_TECH_BRANDS = frozenset({
    "Apple", "Google", "Microsoft", "Amazon", "Netflix", "Facebook", "Instagram",
    "Twitter", "YouTube", "TikTok", "Spotify", "Uber", "Airbnb", "Tesla",
    "SpaceX", "OpenAI", "ChatGPT", "WhatsApp", "Telegram", "Snapchat",
    "Reddit", "LinkedIn", "Pinterest", "eBay", "PayPal", "Zoom", "Slack",
    "GitHub", "Adobe", "Intel", "AMD", "Nvidia", "Samsung", "Sony", "LG",
    "Philips", "Bosch", "Siemens", "IKEA", "McDonald's", "Starbucks",
    "Coca-Cola", "Pepsi", "Nike", "Adidas", "Puma", "Gucci", "Prada",
    "Chanel", "Louis Vuitton", "Versace", "Armani",
})

# universities / known institutions
_INSTITUTIONS = frozenset({
    "USC", "UCLA", "Harvard", "MIT", "Stanford", "Oxford", "Cambridge",
    "Yale", "Princeton", "Columbia", "NYU", "University of Southern California",
})

# regex: capitalized sequence that looks like a proper name  (2+ words OR a single
# word that is not a sentence start — heuristic: preceded by ", " or "- ")
_PROPER_NAME_MULTI = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)(?:\s+Jr\.?|\s+Sr\.?|\s+III?)?\b"
)
_PROPER_NAME_SINGLE = re.compile(r"\b([A-Z][a-zA-Z]{2,})\b")

# suffix patterns preserved in names
_NAME_SUFFIX = re.compile(r"\b(Jr\.?|Sr\.?|II|III|IV)\b")

# Known transliteration mappings source→target for common first names
# These are intentionally small — the engine trusts translation for the rest.
_NAME_TRANSLIT: dict[str, dict[str, str]] = {
    "George": {"ru": "Джордж", "uk": "Джордж", "de": "George"},
    "John": {"ru": "Джон", "uk": "Джон", "de": "John"},
    "Mary": {"ru": "Мэри", "uk": "Мері", "de": "Mary"},
    "Michael": {"ru": "Майкл", "uk": "Майкл", "de": "Michael"},
    "Robert": {"ru": "Роберт", "uk": "Роберт", "de": "Robert"},
    "David": {"ru": "Дэвид", "uk": "Девід", "de": "David"},
    "William": {"ru": "Уильям", "uk": "Вільям", "de": "William"},
    "James": {"ru": "Джеймс", "uk": "Джеймс", "de": "James"},
    "Charles": {"ru": "Чарльз", "uk": "Чарльз", "de": "Charles"},
    "Thomas": {"ru": "Томас", "uk": "Томас", "de": "Thomas"},
    "Richard": {"ru": "Ричард", "uk": "Річард", "de": "Richard"},
    "Daniel": {"ru": "Дэниэл", "uk": "Деніел", "de": "Daniel"},
    "Matthew": {"ru": "Мэтью", "uk": "Метью", "de": "Matthew"},
    "Joseph": {"ru": "Джозеф", "uk": "Джозеф", "de": "Joseph"},
    "Andrew": {"ru": "Эндрю", "uk": "Ендрю", "de": "Andrew"},
    "Kevin": {"ru": "Кевин", "uk": "Кевін", "de": "Kevin"},
    "Mark": {"ru": "Марк", "uk": "Марк", "de": "Mark"},
    "Paul": {"ru": "Пол", "uk": "Пол", "de": "Paul"},
    "Steven": {"ru": "Стивен", "uk": "Стівен", "de": "Steven"},
    "Jr": {"ru": "Молодший", "uk": "Молодший", "de": "Junior"},
    "Junior": {"ru": "Молодший", "uk": "Молодший", "de": "Junior"},
}

# Geographic names (rough transliteration expectations)
_GEO_TRANSLIT: dict[str, dict[str, str]] = {
    "California": {"ru": "Калифорния", "uk": "Каліфорнія", "de": "Kalifornien"},
    "New York": {"ru": "Нью-Йорк", "uk": "Нью-Йорк", "de": "New York"},
    "Los Angeles": {"ru": "Лос-Анджелес", "uk": "Лос-Анджелес", "de": "Los Angeles"},
    "Hollywood": {"ru": "Голливуд", "uk": "Голлівуд", "de": "Hollywood"},
    "Washington": {"ru": "Вашингтон", "uk": "Вашингтон", "de": "Washington"},
    "Chicago": {"ru": "Чикаго", "uk": "Чикаго", "de": "Chicago"},
    "Texas": {"ru": "Техас", "uk": "Техас", "de": "Texas"},
    "Florida": {"ru": "Флорида", "uk": "Флорида", "de": "Florida"},
    "America": {"ru": "Америка", "uk": "Америка", "de": "Amerika"},
    "England": {"ru": "Англия", "uk": "Англія", "de": "England"},
    "London": {"ru": "Лондон", "uk": "Лондон", "de": "London"},
    "Paris": {"ru": "Париж", "uk": "Париж", "de": "Париж"},
    "Italy": {"ru": "Италия", "uk": "Італія", "de": "Italien"},
    "Italian": {"ru": "итальянский", "uk": "італійський", "de": "italienisch"},
    "France": {"ru": "Франция", "uk": "Франція", "de": "Frankreich"},
    "Germany": {"ru": "Германия", "uk": "Німеччина", "de": "Deutschland"},
}


def extract_entities(source_text: str, tgt_lang: str = "uk") -> list[EntityInfo]:
    """
    Detect named entities in the source (English) text.

    Returns EntityInfo list, ordered by first occurrence.
    """
    entities: list[EntityInfo] = []
    seen: set[str] = set()
    base_lang = (tgt_lang or "uk").split("-")[0].lower()

    def _add(text: str, label: str, translation: str = "") -> None:
        key = text.lower()
        if key not in seen:
            seen.add(key)
            entities.append(EntityInfo(
                text=text,
                label=label,
                translation=translation or text,
                protected=True,
            ))

    # 1. Car brands
    for brand in _CAR_BRANDS:
        if re.search(r'\b' + re.escape(brand) + r'\b', source_text, re.IGNORECASE):
            _add(brand, "CAR", brand)

    # 2. Tech / consumer brands
    for brand in _TECH_BRANDS:
        if re.search(r'\b' + re.escape(brand) + r'\b', source_text, re.IGNORECASE):
            _add(brand, "BRAND", brand)

    # 3. Institutions
    for inst in _INSTITUTIONS:
        if re.search(r'\b' + re.escape(inst) + r'\b', source_text, re.IGNORECASE):
            _add(inst, "ORG", inst)

    # 4. Geographic names
    for eng, translit in _GEO_TRANSLIT.items():
        if re.search(r'\b' + re.escape(eng) + r'\b', source_text, re.IGNORECASE):
            translation = translit.get(base_lang, eng)
            _add(eng, "GEO", translation)

    # 5. Multi-word proper names (heuristic — 2+ capitalized words)
    for m in _PROPER_NAME_MULTI.finditer(source_text):
        name = m.group(0).strip()
        # Resolve known name parts
        parts = name.split()
        translated_parts = []
        for part in parts:
            clean = part.strip(".,")
            translit = _NAME_TRANSLIT.get(clean, {})
            translated_parts.append(translit.get(base_lang, part))
        translation = " ".join(translated_parts)
        _add(name, "PERSON", translation)

    return entities


def entity_context_summary(entities: list[EntityInfo]) -> str:
    """Short human-readable summary of found entities."""
    if not entities:
        return "no entities"
    return "; ".join(f"{e.text}→{e.label}:{e.translation}" for e in entities[:6])


def protect_entities_in_translation(
    source_text: str,
    translated_text: str,
    entities: list[EntityInfo],
    tgt_lang: str = "uk",
) -> tuple[str, list[str]]:
    """
    Verify entities are correctly represented in the translation.
    Restore any that are missing.

    Returns (corrected_text, list_of_notes).
    """
    notes: list[str] = []
    text = translated_text

    for ent in entities:
        if not ent.protected:
            continue

        expected = ent.translation or ent.text
        # Check if the entity is present (case-insensitive)
        if re.search(re.escape(expected), text, re.IGNORECASE):
            continue  # ✓ already present

        # Entity missing — check if the original form is present instead
        if ent.label in ("CAR", "BRAND", "ORG") and re.search(
            re.escape(ent.text), text, re.IGNORECASE
        ):
            continue  # ✓ original form kept (ok for brands)

        # Try inserting the translated form where the English form might appear
        if re.search(re.escape(ent.text), text, re.IGNORECASE):
            corrected = re.sub(
                re.escape(ent.text), expected, text, flags=re.IGNORECASE, count=1
            )
            if corrected != text:
                notes.append(f"entity_restored:{ent.text}→{expected}")
                text = corrected

    return text, notes


def validate_entities(
    translated_text: str,
    entities: list[EntityInfo],
) -> tuple[bool, list[str]]:
    """
    Returns (ok, notes).
    ok=False means at least one protected entity is completely absent.
    """
    notes: list[str] = []
    for ent in entities:
        if not ent.protected:
            continue
        expected = ent.translation or ent.text
        # For brands / cars, either form is acceptable
        has_original = bool(re.search(re.escape(ent.text), translated_text, re.IGNORECASE))
        has_translated = bool(re.search(re.escape(expected), translated_text, re.IGNORECASE))
        if not has_original and not has_translated:
            notes.append(f"missing_entity:{ent.text}")
    return len(notes) == 0, notes
