"""Unit tests for canonical normalize_lang."""

from __future__ import annotations

import pytest

from engines.utils.lang_utils import normalize_lang


@pytest.mark.parametrize(
    "raw,default,expected",
    [
        (None, "en", "en"),
        ("", "ru", "ru"),
        ("eng", "en", "en"),
        ("rus", "en", "ru"),
        ("uk-UA", "en", "uk"),
        ("zh-CN", "en", "zh"),
        ("zh_tw", "en", "zh"),
        ("DE", "en", "de"),
    ],
)
def test_normalize_lang(raw, default, expected):
    assert normalize_lang(raw, default=default) == expected


def test_matches_semantic_adaptation_behavior():
    from engines.semantic_adaptation import _normalize_lang

    assert _normalize_lang("zh-cn") == "zh"
    assert _normalize_lang(None) == "en"


def test_matches_naturalizer_default():
    from engines.translation_naturalizer import _normalize_lang

    assert _normalize_lang(None) == "ru"
