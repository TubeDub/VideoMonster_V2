"""Correct common Whisper ASR errors in Chinese drama dialogue.

Evidence: `_tmp_uk_fix/asr_zh.json` + `scripts/fix_zh_uk_desktop_clip.py`.
Homophone / near-miss substitutions destroy MT cues (怀孕/绑架/单传).
"""

from __future__ import annotations

import re
from typing import Any, Sequence

# Exact phrase replacements (Whisper → intended)
_PHRASE_FIXES: dict[str, str] = {
    "我们陆家八代单纯": "我们陆家八代单传",
    "我们入家八代单纯": "我们陆家八代单传",
    "陆家八代单纯": "陆家八代单传",
    "入家八代单纯": "陆家八代单传",
    "八代单纯": "八代单传",
    "自私担保": "子嗣难保",
    "此时单保": "子嗣难保",
    "此次担保": "子嗣难保",
    "入家有后了": "陆家有后了",
    "我和娶妻妃同时被绑架": "我和曲妃妃同时被绑架",
    "娶妻妃": "曲妃妃",
    "要是能一举得难": "要是能一举得男",
    "要是能一几个男": "要是能一举得男",
    "这是我一身的": "这是我一生的",
    "无论是人是你": "无论是男是女",
    "无论是你": "无论是男是女",
    "陆下八代单纯": "陆家八代单传",
    "陆下有厚了": "陆家有后了",
    "陆下有后了": "陆家有后了",
    "有惊呀": "如今啊",
    "绑费": "绑匪",
    "绑子": "绑匪",
    "群辞非": "曲妃妃",
    "内腕他营喝绑子": "那晚她迎合绑匪",
    "内腕他营喝绑匪": "那晚她迎合绑匪",
    "你跟月子前一场义外": "是一个月之前的一场意外",
    "你跟月子前的意外": "一个月之前的意外",
    "一日月前": "一个月前",
}

# Substring replacements (order matters — longer first)
_SUBSTRING_FIXES: list[tuple[str, str]] = sorted(
    (
        ("八代单纯", "八代单传"),
        ("自私担保", "子嗣难保"),
        ("此时单保", "子嗣难保"),
        ("此次担保", "子嗣难保"),
        ("入家八代", "陆家八代"),
        ("入家有后", "陆家有后"),
        ("娶妻妃", "曲妃妃"),
        ("一举得难", "一举得男"),
        ("一几个男", "一举得男"),
        ("我一身的", "我一生的"),
        ("是人是你", "是男是女"),
        ("陆下有厚", "陆家有后"),
        ("陆下有后", "陆家有后"),
        ("陆下八代", "陆家八代"),
        ("绑费的", "绑匪的"),
        ("绑子", "绑匪"),
        ("绑费", "绑匪"),
        ("义外", "意外"),
    ),
    key=lambda x: -len(x[0]),
)

# Speaker-turn markers — do not glue across these when merging
CJK_TURN_BREAK_BEFORE = frozenset(
    {
        "妈",
        "喜欢哥哥",
        "我怀孕了",
        "一个月前",
        "一个月之前的意外",
        "是一个月之前的一场意外",
    }
)


def correct_zh_asr_text(text: str) -> str:
    """Apply phrase then substring ASR fixes to a single ZH line."""
    t = str(text or "").strip()
    if not t:
        return t
    if t in _PHRASE_FIXES:
        return _PHRASE_FIXES[t]
    out = t
    for bad, good in _SUBSTRING_FIXES:
        if bad in out:
            out = out.replace(bad, good)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def correct_zh_asr_segments(
    segments: Sequence[str],
    *,
    language: str | None = None,
) -> list[str]:
    """Correct a list of STT lines when source looks Chinese."""
    lang = str(language or "").split("-")[0].lower()
    texts = [str(s or "") for s in (segments or [])]
    cjk_n = sum(1 for t in texts for ch in t if "\u4e00" <= ch <= "\u9fff")
    if lang not in ("zh", "yue", "zh-cn", "zh-tw", "") and cjk_n < 6:
        return texts
    if lang and lang not in ("zh", "yue", "ja", "ko") and cjk_n < 8:
        return texts
    return [correct_zh_asr_text(t) for t in texts]


def correct_zh_asr_timing_map(
    timing_map: Sequence[Any],
    *,
    language: str | None = None,
) -> list[Any]:
    """Correct `text` fields inside timing_map dicts when present."""
    out: list[Any] = []
    for item in timing_map or []:
        if isinstance(item, dict) and "text" in item:
            fixed = dict(item)
            fixed["text"] = correct_zh_asr_text(str(item.get("text") or ""))
            out.append(fixed)
        else:
            out.append(item)
    return out


def is_cjk_turn_break(text: str) -> bool:
    """True when this cue should start a new dialogue turn."""
    t = str(text or "").strip()
    if not t:
        return False
    if t in CJK_TURN_BREAK_BEFORE:
        return True
    # Short vocatives / revelation openers
    if t in ("妈", "爸", "娘", "哥", "姐"):
        return True
    return False
