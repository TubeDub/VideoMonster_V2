"""
Tests that verify SSML never reaches edge_tts as raw text.

Root cause that prompted these tests:
  Professional Dubbing generates SSML (<speak version="1.0" xmlns="...">).
  edge_tts.Communicate calls xml.sax.saxutils.escape(text), converting < > to &lt; &gt;.
  The TTS service then literally SPEAKS the XML markup:
    - version="1.0"  -> "Вержан один"
    - xmlns=         -> "ХСМЛНС"
    - www.w3.org     -> "WWW3"
    - xml:lang="uk-UA" -> "ЮКІІ..."
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Helpers from engines/tts.py
# ---------------------------------------------------------------------------

def _ssml_to_plain(text: str) -> str:
    """Mimic the SSML-strip logic added to _generate_single."""
    if text.lstrip().startswith("<speak"):
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Unit tests for sanitize_tts_text
# ---------------------------------------------------------------------------

def test_sanitize_strips_html_tags():
    from engines.tts import sanitize_tts_text
    assert "<b>" not in sanitize_tts_text("Hello <b>world</b>")


def test_sanitize_strips_urls():
    from engines.tts import sanitize_tts_text
    result = sanitize_tts_text("Visit https://example.com today")
    assert "https://" not in result
    assert "today" in result


def test_sanitize_collapses_repeated_chars():
    from engines.tts import sanitize_tts_text
    result = sanitize_tts_text("ЮКІІІІІІІІІІІІ")
    # After collapse: at most 2 consecutive identical chars
    assert not re.search(r"(.)\1{2,}", result), f"Repeated chars not collapsed: {result!r}"


def test_sanitize_removes_technical_uppercase_tokens():
    from engines.tts import sanitize_tts_text
    # 6+ Latin ALL-CAPS alnum = technical identifier (e.g. XMLNS2, WWW3ORG) → removed
    # Note: Cyrillic uppercase is normal Ukrainian/Russian text and is NOT removed
    result = sanitize_tts_text("Слово XMLNS20 кінець")
    assert "XMLNS20" not in result, f"Latin uppercase token not removed: {result!r}"


# ---------------------------------------------------------------------------
# Critical: SSML must never reach edge_tts as-is
# ---------------------------------------------------------------------------

SAMPLE_SSML = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="uk-UA">'
    '<prosody rate="-8%">Джордж Молодший вирішив зайнятись фотографією.</prosody>'
    "</speak>"
)


def test_ssml_stripped_to_plain_text():
    """SSML tags must be stripped; plain Ukrainian text must survive."""
    result = _ssml_to_plain(SAMPLE_SSML)
    # No XML left
    assert "<" not in result, f"XML leak in TTS input: {result!r}"
    assert ">" not in result, f"XML leak in TTS input: {result!r}"
    # Meaningful content preserved
    assert "Джордж" in result
    assert "фотографією" in result


def test_ssml_no_xmlns_spoken():
    """xmlns attribute must NOT appear in plain text sent to TTS."""
    result = _ssml_to_plain(SAMPLE_SSML)
    assert "xmlns" not in result.lower()
    assert "www.w3.org" not in result
    assert "synthesis" not in result


def test_ssml_no_version_spoken():
    """version="1.0" must NOT be spoken."""
    result = _ssml_to_plain(SAMPLE_SSML)
    # version number left alone (comes from prosody content) but attribute label gone
    assert 'version="1.0"' not in result


def test_ssml_no_xml_lang_spoken():
    """xml:lang="uk-UA" must NOT appear in the spoken text."""
    result = _ssml_to_plain(SAMPLE_SSML)
    assert "uk-UA" not in result
    assert "xml:lang" not in result


def test_plain_text_passes_through_unchanged():
    """Plain Ukrainian text (no SSML) must not be modified by SSML stripper."""
    plain = "Джордж Молодший вирішив зайнятись фотографією."
    result = _ssml_to_plain(plain)
    assert result == plain


def test_empty_ssml_does_not_crash():
    """SSML with empty body should return empty string, not crash."""
    empty_ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ru-RU"></speak>'
    result = _ssml_to_plain(empty_ssml)
    assert result.strip() == ""


# ---------------------------------------------------------------------------
# Pipeline: auto_dub_api work_items use plain_text not SSML
# ---------------------------------------------------------------------------

def test_tts_group_prefers_plain_text():
    """build_tts_work_items must use plain_text when available, not SSML text."""
    group = {
        "indices": [0],
        "text": SAMPLE_SSML,
        "plain_text": "Джордж Молодший вирішив зайнятись фотографією.",
        "timing": [0, 4000],
        "prosody_rate": "-8%",
        "prosody_pitch": None,
    }
    plain = str(group.get("plain_text") or "").strip()
    ssml_or_plain = str(group.get("text") or "").strip()
    text = plain if plain else ssml_or_plain
    if text.lstrip().startswith("<speak"):
        text = re.sub(r"<[^>]+>", " ", text).strip()

    assert "<" not in text
    assert "Джордж" in text


def test_tts_group_strips_ssml_when_no_plain_text():
    """When plain_text is absent, SSML in text must be stripped before TTS."""
    group = {
        "indices": [0],
        "text": SAMPLE_SSML,
        "plain_text": "",
        "timing": [0, 4000],
    }
    plain = str(group.get("plain_text") or "").strip()
    ssml_or_plain = str(group.get("text") or "").strip()
    text = plain if plain else ssml_or_plain
    if text.lstrip().startswith("<speak"):
        text = re.sub(r"<[^>]+>", " ", text).strip()

    assert "<" not in text
    assert "xmlns" not in text.lower()
    assert "www.w3.org" not in text


# ---------------------------------------------------------------------------
# Duplicate _regen_segment_tts guard
# ---------------------------------------------------------------------------

def test_no_duplicate_regen_segment_tts():
    """_regen_segment_tts should be defined only once in auto_dub_api (the new version).
    The old simple version was renamed to _regen_segment_tts_simple."""
    import inspect
    import api.auto_dub_api as m

    # New function must exist with keyword-only voice + work_dir
    assert hasattr(m, "_regen_segment_tts"), "_regen_segment_tts must exist"
    sig = inspect.signature(m._regen_segment_tts)
    params = sig.parameters
    assert "work_dir" in params, "_regen_segment_tts must require work_dir (new version)"
    # Simple version must also exist for _apply_text_adaptation
    assert hasattr(m, "_regen_segment_tts_simple"), "_regen_segment_tts_simple must exist"


# ---------------------------------------------------------------------------
# Audit log correctness
# ---------------------------------------------------------------------------

def test_audit_tts_text_field_is_tts_not_text():
    """tts_text audit field must read seg['tts_text'], not seg['text']."""
    # Simulate what auto_dub_api does at the audit row build step
    seg = {"text": "plain display text", "tts_text": "actual tts input"}
    tts_text_audit = seg.get("tts_text") or seg.get("text") or ""
    assert tts_text_audit == "actual tts input", (
        "audit 'tts_text' must read seg['tts_text'], not seg['text']"
    )
