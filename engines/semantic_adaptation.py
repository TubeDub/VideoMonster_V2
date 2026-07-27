"""Universal semantic adaptation of dubbing text to segment duration."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.semantic_adaptation")

# Символов речи в секунду (оценка Edge-TTS) — универсальные коэффициенты по семействам
_CHARS_PER_SEC: dict[str, float] = {
    "zh": 4.5,
    "ja": 5.5,
    "ko": 5.0,
    "ar": 10.0,
    "hi": 11.0,
    "default": 13.5,
}

_MIN_CHARS_RATIO = 0.38
_MIN_WORDS = 2


@dataclass
class AdaptationRecord:
    index: int
    source_text: str
    original_translation: str
    adapted_text: str
    reason: str
    chars_before: int
    chars_after: int
    words_before: int
    words_after: int
    window_ms: int
    estimated_tts_ms: int
    final_tts_ms: int = 0
    quality_ok: bool = True
    quality_note: str = ""
    tgt_lang: str = ""
    src_lang: str = ""

    def to_log_line(self) -> str:
        def esc(s: str) -> str:
            return (s or "").replace("\t", " ").replace("\n", " ").strip()[:400]

        return (
            f"idx={self.index}\t"
            f"src_lang={self.src_lang}\t"
            f"tgt_lang={self.tgt_lang}\t"
            f"source={esc(self.source_text)!r}\t"
            f"original={esc(self.original_translation)!r}\t"
            f"adapted={esc(self.adapted_text)!r}\t"
            f"reason={esc(self.reason)}\t"
            f"chars={self.chars_before}->{self.chars_after}\t"
            f"words={self.words_before}->{self.words_after}\t"
            f"window_ms={self.window_ms}\t"
            f"est_tts_ms={self.estimated_tts_ms}\t"
            f"final_tts_ms={self.final_tts_ms}\t"
            f"quality={self.quality_ok}\t"
            f"note={esc(self.quality_note)}"
        )


class SemanticAdaptationLog:
    LOG_NAME = "semantic_adaptation.log"

    def __init__(self, app_dir: Path, task_id: str = ""):
        self.app_dir = app_dir
        self.task_id = task_id
        self.log_dir = app_dir / "output" / "dev"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / self.LOG_NAME
        self._records: list[AdaptationRecord] = []

    @property
    def path(self) -> str:
        return str(self.log_path)

    def add(self, record: AdaptationRecord) -> None:
        self._records.append(record)

    def update_final_tts_ms(self, index: int, tts_ms: int) -> None:
        for rec in self._records:
            if rec.index == index:
                rec.final_tts_ms = int(tts_ms)

    def flush(self, **extra) -> str:
        if not self._records:
            return str(self.log_path)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        header = [f"=== task={self.task_id} ts={ts} segments={len(self._records)} ==="]
        for k, v in extra.items():
            header.append(f"{k}={v}")
        header.append(
            "idx\tsrc_lang\ttgt_lang\tsource\toriginal\tadapted\treason\t"
            "chars\twords\twindow_ms\test_tts_ms\tfinal_tts_ms\tquality\tnote"
        )
        body = header + [
            r.to_log_line() for r in sorted(self._records, key=lambda x: x.index)
        ]
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(body) + "\n\n")
        return str(self.log_path)


from engines.utils.lang_utils import normalize_lang as _normalize_lang


def _chars_per_sec(lang: str) -> float:
    base = _normalize_lang(lang)
    return _CHARS_PER_SEC.get(base, _CHARS_PER_SEC["default"])


def estimate_tts_duration_ms(text: str, lang: str) -> int:
    """Universal speech duration estimate (no audio synthesis)."""
    t = str(text or "").strip()
    if not t:
        return 0
    cps = _chars_per_sec(lang)
    char_ms = (len(t) / cps) * 1000.0
    words = len(t.split())
    word_ms = (words / 2.6) * 1000.0
    return int(max(char_ms, word_ms))


def validate_adaptation_quality(
    original: str,
    adapted: str,
    *,
    source_hint: str = "",
    tgt_lang: str = "ru",
) -> tuple[bool, str]:
    """
    Quality gate before TTS — word/time based, not char ratio (TZ §4).
    """
    from engines.semantic_meaning import (
        is_truncated_adaptation,
        verify_meaning_preserved,
        word_count,
    )

    orig = str(original or "").strip()
    adpt = str(adapted or "").strip()
    if not adpt:
        return False, "empty_adapted"
    if not orig:
        return True, "ok"
    if adpt == orig:
        return True, "unchanged"

    if is_truncated_adaptation(orig, adpt):
        return False, "truncated_tail"

    ok, reason, _ = verify_meaning_preserved(
        source_hint or orig,
        orig,
        adpt,
        target_lang=tgt_lang,
    )
    if not ok:
        return False, reason

    ow, aw = word_count(orig), word_count(adpt)
    if ow >= 6 and aw < max(_MIN_WORDS, int(ow * 0.5)):
        return False, "over_shortened_words"

    if adpt[-1] in ",;:—–-" and ow >= 5:
        return False, "broken_phrase"

    return True, "ok"


def adapt_text_to_window(
    text: str,
    window_ms: int,
    *,
    source_hint: str = "",
    src_lang: str = "en",
    tgt_lang: str = "ru",
    index: int = 0,
) -> tuple[str, AdaptationRecord | None]:
    """
    Proactive semantic adaptation before TTS.
    Priority: meaning → natural shorter phrasing → fit window (no speed change here).
    """
    from engines.translation_adapt import adapt_for_duration, adapt_translation_shorter

    original = " ".join(str(text or "").split())
    if not original or window_ms <= 0:
        return original, None

    est_ms = estimate_tts_duration_ms(original, tgt_lang)
    target_ms = max(200, int(window_ms) - 40)

    if est_ms <= target_ms * 1.04:
        return original, None

    ratio = max(0.55, min(0.95, target_ms / max(est_ms, 1)))
    reason = f"pre_tts_est_overflow est={est_ms}ms window={target_ms}ms"

    adapted = adapt_translation_shorter(
        original,
        target_ratio=ratio,
        source_hint=source_hint,
        allow_llm=True,
        stage="auto",
        tgt_lang=tgt_lang,
    )

    if adapted == original:
        adapted = adapt_for_duration(
            original,
            est_ms,
            target_ms,
            source_hint,
            stage="strong",
        )
        reason = f"pre_tts_duration_fit est={est_ms}ms window={target_ms}ms"

    ok, note = validate_adaptation_quality(
        original, adapted, source_hint=source_hint, tgt_lang=tgt_lang
    )
    if not ok:
        logger.warning(
            "[SemanticAdapt] idx=%d rejected (%s) — keeping original",
            index,
            note,
        )
        return original, AdaptationRecord(
            index=index,
            source_text=source_hint,
            original_translation=original,
            adapted_text=original,
            reason=f"rejected_{note}",
            chars_before=len(original),
            chars_after=len(original),
            words_before=len(original.split()),
            words_after=len(original.split()),
            window_ms=target_ms,
            estimated_tts_ms=est_ms,
            quality_ok=False,
            quality_note=note,
            tgt_lang=_normalize_lang(tgt_lang),
            src_lang=_normalize_lang(src_lang),
        )

    if adapted == original:
        return original, None

    new_est = estimate_tts_duration_ms(adapted, tgt_lang)
    record = AdaptationRecord(
        index=index,
        source_text=source_hint,
        original_translation=original,
        adapted_text=adapted,
        reason=reason,
        chars_before=len(original),
        chars_after=len(adapted),
        words_before=len(original.split()),
        words_after=len(adapted.split()),
        window_ms=target_ms,
        estimated_tts_ms=new_est,
        quality_ok=True,
        quality_note=note,
        tgt_lang=_normalize_lang(tgt_lang),
        src_lang=_normalize_lang(src_lang),
    )
    logger.info(
        "[SemanticAdapt] idx=%d %d→%d chars est %d→%d ms window=%d",
        index,
        record.chars_before,
        record.chars_after,
        est_ms,
        new_est,
        target_ms,
    )
    return adapted, record


def _window_ms_from_timing(timing: Any, *, margin_ms: int = 40) -> int:
    if isinstance(timing, (list, tuple)) and len(timing) >= 2:
        return max(200, int(timing[1]) - int(timing[0]) - margin_ms)
    if isinstance(timing, dict):
        return max(200, int(timing.get("end", 0)) - int(timing.get("start", 0)) - margin_ms)
    return 5000


def prepare_tts_groups_semantic(
    tts_groups: list[dict],
    *,
    source_segments: list[str],
    src_lang: str,
    tgt_lang: str,
    task_id: str,
    app_dir: Path,
    adapt_text: bool = True,
    segments_data: list | None = None,
) -> tuple[list[dict], SemanticAdaptationLog]:
    """Adapt each TTS group text to its timing window before synthesis.

    When adapt_text=False, groups pass through unchanged (TTS uses approved Final).
    When text is shortened, also update ``plain_text`` and stamp the head
    segment so Review/audits stay aligned with what TTS will speak.
    """
    log = SemanticAdaptationLog(app_dir, task_id=task_id)
    if not adapt_text:
        return list(tts_groups), log

    out_groups: list[dict] = []

    for group in tts_groups:
        text = str(group.get("text") or "").strip()
        indices = group.get("indices") or [0]
        head = int(indices[0]) if indices else 0
        src_hint = " ".join(
            str(source_segments[i] or "").strip()
            for i in indices
            if i < len(source_segments) and str(source_segments[i] or "").strip()
        ).strip()
        window = _window_ms_from_timing(group.get("timing"))

        adapted, rec = adapt_text_to_window(
            text,
            window,
            source_hint=src_hint,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            index=head,
        )
        if rec:
            log.add(rec)
        updated = {**group, "text": adapted}
        if adapted and adapted != text:
            updated["plain_text"] = adapted
            if (
                segments_data is not None
                and 0 <= head < len(segments_data)
                and isinstance(segments_data[head], dict)
            ):
                try:
                    from engines.translation_validation import (
                        stamp_authoritative_final_text,
                    )

                    stamp_authoritative_final_text(segments_data[head], adapted)
                except Exception:
                    segments_data[head]["text"] = adapted
                    segments_data[head]["plain_text"] = adapted
                    segments_data[head]["tts_text"] = adapted
                    segments_data[head]["text_for_tts"] = adapted
        out_groups.append(updated)

    return out_groups, log


def record_post_tts_adaptation(
    log: SemanticAdaptationLog | None,
    *,
    index: int,
    source_hint: str,
    original: str,
    adapted: str,
    reason: str,
    window_ms: int,
    tts_ms_before: int,
    tts_ms_after: int = 0,
    src_lang: str = "",
    tgt_lang: str = "",
) -> None:
    if log is None:
        return
    ok, note = validate_adaptation_quality(original, adapted, source_hint=source_hint, tgt_lang=tgt_lang)
    log.add(
        AdaptationRecord(
            index=index,
            source_text=source_hint,
            original_translation=original,
            adapted_text=adapted,
            reason=reason,
            chars_before=len(original),
            chars_after=len(adapted),
            words_before=len(original.split()),
            words_after=len(adapted.split()),
            window_ms=window_ms,
            estimated_tts_ms=tts_ms_before,
            final_tts_ms=tts_ms_after or tts_ms_before,
            quality_ok=ok,
            quality_note=note,
            src_lang=_normalize_lang(src_lang),
            tgt_lang=_normalize_lang(tgt_lang),
        )
    )
