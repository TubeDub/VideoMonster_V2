"""
Content Mode — project type detection and mode-specific dubbing parameters.

Supported modes:
  movie      Strict lip-sync, preserve silences, no cross-pause text
  tv_series  Like movie but slightly more tolerant
  anime      Movie-like but allows faster speech (anime vocal style)
  cartoon    Relaxed timing, cheerful pace
  youtube    Blogger-like, natural flow, allow small merges
  blogger    Continuous natural speech, uniform pace
  podcast    Slow clear speech, long pauses preserved
  interview  Question-answer blocks treated as separate units
  audiobook  Slowest pace, maximum clarity
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Mode identifiers ───────────────────────────────────────────────────────────

class ContentMode(str, Enum):
    MOVIE      = "movie"
    TV_SERIES  = "tv_series"
    ANIME      = "anime"
    CARTOON    = "cartoon"
    YOUTUBE    = "youtube"
    BLOGGER    = "blogger"
    PODCAST    = "podcast"
    INTERVIEW  = "interview"
    AUDIOBOOK  = "audiobook"

    @classmethod
    def from_str(cls, s: "ContentMode | str") -> "ContentMode":
        # If already a ContentMode instance, return it directly
        if isinstance(s, cls):
            return s
        # Use .value if it's a str-Enum to avoid "ClassName.MEMBER" serialisation
        raw = s.value if hasattr(s, "value") else str(s)
        try:
            return cls(raw.lower().strip())
        except ValueError:
            return cls.MOVIE  # safe default


# ── Per-mode dubbing parameters ────────────────────────────────────────────────

@dataclass
class ModeProfile:
    """Tunable parameters for each content mode."""
    mode: ContentMode

    # Timing
    slot_tolerance_pct: float = 10.0   # % over slot considered OK without adaptation
    max_atempo: float = 1.15            # voice quality gate threshold
    video_adapt_threshold: float = 10.0 # % overflow → prefer video speed adjust
    allow_merge: bool = True            # merge adjacent blocks if needed
    max_merge_blocks: int = 2           # max blocks to merge (always ≤ 2 per spec)
    strict_pause_preservation: bool = False  # never place text in big pauses

    # Text adaptation aggressiveness
    min_word_retention: float = 0.60   # minimum word retention after adaptation
    sso_max_level: int = 5

    # Speech
    equalize_speeds: bool = True        # normalize speed between adjacent segments
    natural_pause_min_ms: int = 80
    natural_pause_max_ms: int = 200

    # Audio
    ducking_enabled: bool = True
    ducking_level_db: float = -8.0      # how much to duck background during speech
    ducking_fade_in_ms: int = 150
    ducking_fade_out_ms: int = 300

    # Silence handling
    min_silence_preserve_ms: int = 500  # pauses larger than this → never fill with text
    max_artificial_silence_ms: int = 300 # no silent padding beyond this


_MOVIE = ModeProfile(
    mode=ContentMode.MOVIE,
    slot_tolerance_pct=5.0,
    max_atempo=1.12,
    video_adapt_threshold=7.0,
    strict_pause_preservation=True,
    min_word_retention=0.65,
    equalize_speeds=True,
    ducking_level_db=-6.0,
    min_silence_preserve_ms=400,
    max_artificial_silence_ms=100,
)

_TV_SERIES = ModeProfile(
    mode=ContentMode.TV_SERIES,
    slot_tolerance_pct=8.0,
    max_atempo=1.13,
    strict_pause_preservation=True,
    min_word_retention=0.62,
    min_silence_preserve_ms=400,
)

_ANIME = ModeProfile(
    mode=ContentMode.ANIME,
    slot_tolerance_pct=7.0,
    max_atempo=1.18,   # anime can be slightly faster
    strict_pause_preservation=True,
    min_word_retention=0.60,
)

_CARTOON = ModeProfile(
    mode=ContentMode.CARTOON,
    slot_tolerance_pct=12.0,
    max_atempo=1.20,
    strict_pause_preservation=False,
    min_word_retention=0.58,
    natural_pause_min_ms=60,
)

_YOUTUBE = ModeProfile(
    mode=ContentMode.YOUTUBE,
    slot_tolerance_pct=12.0,
    max_atempo=1.15,
    strict_pause_preservation=False,
    min_word_retention=0.60,
    min_silence_preserve_ms=600,
)

_BLOGGER = ModeProfile(
    mode=ContentMode.BLOGGER,
    slot_tolerance_pct=15.0,
    max_atempo=1.15,
    strict_pause_preservation=False,
    min_word_retention=0.58,
    equalize_speeds=True,
    min_silence_preserve_ms=600,
    max_artificial_silence_ms=400,
)

_PODCAST = ModeProfile(
    mode=ContentMode.PODCAST,
    slot_tolerance_pct=20.0,
    max_atempo=1.10,
    strict_pause_preservation=True,
    min_word_retention=0.70,
    equalize_speeds=True,
    natural_pause_min_ms=150,
    natural_pause_max_ms=350,
    min_silence_preserve_ms=800,
)

_INTERVIEW = ModeProfile(
    mode=ContentMode.INTERVIEW,
    slot_tolerance_pct=15.0,
    max_atempo=1.12,
    strict_pause_preservation=True,
    allow_merge=False,   # Q&A blocks must stay separate
    min_word_retention=0.65,
    min_silence_preserve_ms=600,
)

_AUDIOBOOK = ModeProfile(
    mode=ContentMode.AUDIOBOOK,
    slot_tolerance_pct=25.0,
    max_atempo=1.05,
    strict_pause_preservation=True,
    min_word_retention=0.75,
    natural_pause_min_ms=200,
    natural_pause_max_ms=500,
    min_silence_preserve_ms=1000,
    ducking_enabled=False,
)

_PROFILES: dict[ContentMode, ModeProfile] = {
    ContentMode.MOVIE:     _MOVIE,
    ContentMode.TV_SERIES: _TV_SERIES,
    ContentMode.ANIME:     _ANIME,
    ContentMode.CARTOON:   _CARTOON,
    ContentMode.YOUTUBE:   _YOUTUBE,
    ContentMode.BLOGGER:   _BLOGGER,
    ContentMode.PODCAST:   _PODCAST,
    ContentMode.INTERVIEW: _INTERVIEW,
    ContentMode.AUDIOBOOK: _AUDIOBOOK,
}


def get_profile(mode: "ContentMode | str") -> ModeProfile:
    if not isinstance(mode, ContentMode):
        mode = ContentMode.from_str(mode)
    return _PROFILES.get(mode, _MOVIE)


# ── Auto-detection ─────────────────────────────────────────────────────────────

def auto_detect_mode(
    video_duration_ms: int = 0,
    segment_count: int = 0,
    avg_segment_ms: float = 0,
    avg_gap_ms: float = 0,
    source_hints: list[str] | None = None,
) -> ContentMode:
    """
    Heuristic content mode detection.
    Falls back to MOVIE for unknown patterns.
    """
    hints_text = " ".join(source_hints or []).lower()

    # Long average segments with big gaps → podcast / interview
    if avg_segment_ms > 8000 and avg_gap_ms > 1500:
        return ContentMode.PODCAST
    if avg_segment_ms > 5000 and avg_gap_ms > 2000:
        return ContentMode.INTERVIEW

    # Very short segments, fast delivery → possibly anime
    if avg_segment_ms < 1500 and segment_count > 50:
        return ContentMode.ANIME

    # Very long video with few segments → audiobook
    if video_duration_ms > 30 * 60 * 1000 and segment_count < 100:
        return ContentMode.AUDIOBOOK

    # Many short segments, continuous → blogger/youtube
    if avg_gap_ms < 500 and segment_count > 30:
        return ContentMode.BLOGGER

    return ContentMode.MOVIE


# ── UI helpers ─────────────────────────────────────────────────────────────────

MODE_LABELS: dict[str, dict[str, str]] = {
    "movie":     {"ru": "🎬 Фильм",        "uk": "🎬 Фільм",       "en": "🎬 Movie",     "de": "🎬 Film"},
    "tv_series": {"ru": "📺 Сериал",        "uk": "📺 Серіал",      "en": "📺 TV Series", "de": "📺 Serie"},
    "anime":     {"ru": "⛩ Аниме",         "uk": "⛩ Аніме",       "en": "⛩ Anime",     "de": "⛩ Anime"},
    "cartoon":   {"ru": "🎨 Мультфильм",    "uk": "🎨 Мультфільм",  "en": "🎨 Cartoon",   "de": "🎨 Zeichentrick"},
    "youtube":   {"ru": "▶ YouTube",        "uk": "▶ YouTube",      "en": "▶ YouTube",   "de": "▶ YouTube"},
    "blogger":   {"ru": "🎙 Блогер",        "uk": "🎙 Блогер",      "en": "🎙 Blogger",   "de": "🎙 Blogger"},
    "podcast":   {"ru": "🎧 Подкаст",       "uk": "🎧 Подкаст",     "en": "🎧 Podcast",   "de": "🎧 Podcast"},
    "interview": {"ru": "🎤 Интервью",      "uk": "🎤 Інтерв'ю",   "en": "🎤 Interview", "de": "🎤 Interview"},
    "audiobook": {"ru": "📖 Аудиокнига",    "uk": "📖 Аудіокнига",  "en": "📖 Audiobook", "de": "📖 Hörbuch"},
}


def mode_label(mode: str, lang: str = "ru") -> str:
    labels = MODE_LABELS.get(mode, {})
    return labels.get(lang, labels.get("en", mode))


def all_modes_for_ui(lang: str = "ru") -> list[dict[str, str]]:
    return [
        {"value": m, "label": mode_label(m, lang)}
        for m in ContentMode
    ]
