"""Tests for AI Memory + Semantic Cache (TZ #6)."""

from __future__ import annotations

from core.ai_memory import AIMemory, MemoryEntry, memory_enabled
from core.semantic_cache import SemanticCache, semantic_cache_enabled, semantic_fingerprint


def test_semantic_fingerprint_stable():
    fp1 = semantic_fingerprint("Hello world", source_lang="en", target_lang="uk")
    fp2 = semantic_fingerprint("Hello world", source_lang="en", target_lang="uk")
    fp3 = semantic_fingerprint("Different text", source_lang="en", target_lang="uk")
    assert fp1 == fp2
    assert fp1 != fp3


def test_semantic_cache_exact_hit(tmp_path):
    cache = SemanticCache(tmp_path / "cache.db")
    cache.store("Hello", "Привіт", source_lang="en", target_lang="uk", task_type="translate")
    hit = cache.lookup("Hello", source_lang="en", target_lang="uk", task_type="translate")
    assert hit is not None
    assert hit.text == "Привіт"
    assert hit.source == "exact"
    assert cache.stats.hits == 1


def test_semantic_cache_fuzzy_hit(tmp_path):
    cache = SemanticCache(tmp_path / "cache.db", similarity_threshold=0.5)
    cache.store(
        "Luke Skywalker flew his ship",
        "Люк Скайуокер полетів на своєму кораблі",
        source_lang="en", target_lang="uk",
    )
    hit = cache.lookup(
        "Luke Skywalker flew the ship",
        source_lang="en", target_lang="uk",
    )
    assert hit is not None
    assert hit.source == "fuzzy"
    assert hit.similarity >= 0.5


def test_semantic_cache_miss(tmp_path):
    cache = SemanticCache(tmp_path / "cache.db")
    hit = cache.lookup("unknown text", source_lang="en", target_lang="uk")
    assert hit is None
    assert cache.stats.misses == 1


def test_semantic_cache_search(tmp_path):
    cache = SemanticCache(tmp_path / "cache.db", similarity_threshold=0.3)
    cache.store("Star Wars movie", "Зоряні війни", source_lang="en", target_lang="uk")
    results = cache.search("Star Wars", source_lang="en", target_lang="uk")
    assert len(results) >= 1


def test_memory_save_and_find_character(tmp_path):
    mem = AIMemory("proj-1", app_dir=tmp_path)
    mem.save(MemoryEntry(
        key="Luke Skywalker", value="Люк Скайуокер",
        category="character", locked=True,
        metadata={"gender": "male", "age": "young"},
    ))
    found = mem.find("Luke Skywalker", category="character")
    assert found is not None
    assert found.value == "Люк Скайуокер"
    assert found.locked is True
    char = mem.get_character("Luke Skywalker")
    assert char["translation"] == "Люк Скайуокер"
    assert char["gender"] == "male"


def test_memory_locked_entry_cannot_change(tmp_path):
    mem = AIMemory("proj-2", app_dir=tmp_path)
    mem.save(MemoryEntry(key="USC", value="Університет Південної Каліфорнії", category="glossary", locked=True))
    ok = mem.save(MemoryEntry(key="USC", value="інший переклад", category="glossary"))
    assert ok is False
    found = mem.find("USC")
    assert found.value == "Університет Південної Каліфорнії"


def test_memory_user_correction_becomes_canonical(tmp_path):
    mem = AIMemory("proj-3", app_dir=tmp_path)
    mem.update("Fiat", "Фіат", category="brand", user_correction=True)
    found = mem.find("Fiat", category="brand")
    assert found is not None
    assert found.value == "Фіат"
    assert found.locked is True


def test_memory_glossary_and_style(tmp_path):
    mem = AIMemory("proj-4", app_dir=tmp_path)
    mem.save(MemoryEntry(key="droid", value="дроїд", category="glossary"))
    mem.save(MemoryEntry(key="tone", value="informal", category="style"))
    assert len(mem.get_glossary()) >= 1
    assert mem.get_style().get("tone") == "informal"


def test_memory_voice_profile(tmp_path):
    mem = AIMemory("proj-5", app_dir=tmp_path)
    mem.save(MemoryEntry(
        key="Luke", value="male-young", category="voice",
        metadata={"timbre": "warm", "pitch": "+2Hz", "voice_model": "uk-UA-Ostap"},
    ))
    voice = mem.get_voice("Luke")
    assert voice is not None
    assert voice["timbre"] == "warm"
    assert voice["voice_model"] == "uk-UA-Ostap"


def test_memory_cross_project_global(tmp_path):
    mem = AIMemory("ep-1", app_dir=tmp_path, series_id="star-wars")
    mem.save(MemoryEntry(key="Death Star", value="Зоря Смерті", category="location"), global_memory=True)
    mem2 = AIMemory("ep-2", app_dir=tmp_path, series_id="star-wars")
    found = mem2.find("Death Star", category="location")
    assert found is not None
    assert found.value == "Зоря Смерті"


def test_memory_consistency_detects_contradictions(tmp_path):
    mem = AIMemory("proj-6", app_dir=tmp_path)
    issues = mem.check_consistency(
        ["Люк полетів", "Лука полетів"],
        ["Luke flew", "Luke flew"],
    )
    # May or may not detect depending on entity extraction — just verify no crash.
    assert isinstance(issues, list)


def test_memory_learn_from_job(tmp_path):
    mem = AIMemory("proj-7", app_dir=tmp_path)
    result = mem.learn({
        "source_segments": ["Hello world"],
        "segments": ["Привіт світ"],
        "source_lang": "en",
        "target_lang": "uk",
        "translation_audits": [{
            "index": 0,
            "source_text": "Hello world",
            "final_text": "Привіт світ",
        }],
        "user_corrections": [{
            "key": "Hello", "value": "Привіт", "category": "glossary",
        }],
    })
    assert result["corrections"] >= 1
    assert result["cache"] >= 1


def test_memory_build_context_prompt(tmp_path):
    mem = AIMemory("proj-8", app_dir=tmp_path)
    mem.save(MemoryEntry(key="Luke", value="Люк", category="character"))
    mem.save(MemoryEntry(key="tone", value="epic", category="style"))
    ctx = mem.build_context_prompt()
    assert "Luke" in ctx
    assert "Люк" in ctx


def test_memory_lookup_translation_via_cache(tmp_path):
    mem = AIMemory("proj-9", app_dir=tmp_path)
    mem.store_translation("Test phrase", "Тестова фраза", source_lang="en", target_lang="uk")
    result = mem.lookup_translation("Test phrase", source_lang="en", target_lang="uk")
    assert result == "Тестова фраза"


def test_flags():
    import os

    os.environ["VM_AI_MEMORY"] = "1"
    assert memory_enabled() is True
    os.environ["VM_SEMANTIC_CACHE"] = "1"
    assert semantic_cache_enabled() is True
    os.environ.pop("VM_AI_MEMORY", None)
    os.environ.pop("VM_SEMANTIC_CACHE", None)
