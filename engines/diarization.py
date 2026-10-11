"""Speaker diarization (spec v3).

Uses PyAnnote 3.x when installed and ``HF_TOKEN`` env is set; falls back to a
single-speaker assignment so downstream stages never break.

Public API:
    ``run_diarization(audio_path, *, hf_token=None) -> DiarizationResult``
    ``assign_speakers_to_segments(segments, dia_result) -> segments``
    ``extract_speaker_reference_clip(audio_path, dia_result, speaker_id, out_wav, *, min_ms=5000)``

Every function is safe to call regardless of PyAnnote availability. Diagnostics
are returned via ``DiarizationResult.to_dict()`` for OpenDDF lineage.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.diarization")


@dataclass
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker: str

    def duration_ms(self) -> int:
        return max(0, int(self.end_ms) - int(self.start_ms))


@dataclass
class DiarizationResult:
    enabled: bool = False
    attempted: bool = False
    success: bool = False
    method: str = "none"
    turns: list[SpeakerTurn] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    speaker_durations_ms: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["turns"] = [asdict(t) for t in self.turns]
        return d


def is_diarization_enabled(info: dict[str, Any] | None = None) -> bool:
    """Opt-in via spec_v3 flag or env VM_DIARIZE=1."""
    if info and (info.get("spec_v3") or info.get("diarize") or info.get("diarization")):
        return True
    return (os.getenv("VM_DIARIZE") or "").strip() in ("1", "true", "yes", "on")


def _hf_token() -> str | None:
    tok = (os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or "").strip()
    return tok or None


def _try_pyannote(audio_path: str, *, hf_token: str | None) -> DiarizationResult | None:
    try:
        from pyannote.audio import Pipeline as _Pipeline
    except Exception as exc:
        logger.info("[diarization] pyannote not available: %s", exc)
        return None

    token = hf_token or _hf_token()
    if not token:
        logger.info("[diarization] HF_TOKEN missing — cannot load pyannote pipeline")
        return DiarizationResult(
            enabled=True,
            attempted=True,
            success=False,
            method="pyannote_missing_token",
            error="hf_token_missing",
            warning="Set HF_TOKEN env variable to enable PyAnnote diarization.",
        )

    started = time.perf_counter()
    try:
        pipeline = _Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=token,
        )
    except Exception as exc:
        logger.warning("[diarization] pyannote load failed: %s", exc)
        return DiarizationResult(
            enabled=True,
            attempted=True,
            success=False,
            method="pyannote_load_failed",
            error=str(exc)[:200],
        )

    try:
        annotation = pipeline(audio_path)
    except Exception as exc:
        logger.warning("[diarization] pyannote inference failed: %s", exc)
        return DiarizationResult(
            enabled=True,
            attempted=True,
            success=False,
            method="pyannote_inference_failed",
            error=str(exc)[:200],
        )

    turns: list[SpeakerTurn] = []
    for segment, _, label in annotation.itertracks(yield_label=True):
        turns.append(
            SpeakerTurn(
                start_ms=int(segment.start * 1000),
                end_ms=int(segment.end * 1000),
                speaker=str(label),
            )
        )
    speakers = sorted({t.speaker for t in turns})
    durations = {sp: sum(t.duration_ms() for t in turns if t.speaker == sp) for sp in speakers}
    dur_total = int((time.perf_counter() - started) * 1000)
    logger.info(
        "[diarization] pyannote ok speakers=%s turns=%s duration_ms=%s",
        len(speakers),
        len(turns),
        dur_total,
    )
    return DiarizationResult(
        enabled=True,
        attempted=True,
        success=True,
        method="pyannote/speaker-diarization-3.1",
        turns=turns,
        speakers=speakers,
        speaker_durations_ms=durations,
        duration_ms=dur_total,
    )


def _single_speaker_fallback(audio_path: str) -> DiarizationResult:
    """When PyAnnote is unavailable, treat the whole track as SPEAKER_00."""
    try:
        from pydub import AudioSegment

        seg = AudioSegment.from_file(audio_path)
        total_ms = int(len(seg))
    except Exception:
        total_ms = 0
    turn = SpeakerTurn(start_ms=0, end_ms=total_ms or 60_000, speaker="SPEAKER_00")
    return DiarizationResult(
        enabled=True,
        attempted=True,
        success=True,
        method="single_speaker_fallback",
        turns=[turn],
        speakers=["SPEAKER_00"],
        speaker_durations_ms={"SPEAKER_00": turn.duration_ms()},
        duration_ms=0,
        warning="pyannote unavailable; using single-speaker fallback (no per-voice profiles)",
    )


def run_diarization(
    audio_path: str,
    *,
    hf_token: str | None = None,
    task_info: dict[str, Any] | None = None,
) -> DiarizationResult:
    """Attempt PyAnnote diarization; fall back to single-speaker.

    Never raises — downstream stages must always receive a usable result.
    """
    if not is_diarization_enabled(task_info):
        return DiarizationResult(
            enabled=False,
            attempted=False,
            method="disabled",
            warning="Diarization disabled (spec_v3/VM_DIARIZE opt-in)",
        )

    if not Path(audio_path).is_file():
        return DiarizationResult(
            enabled=True,
            attempted=True,
            method="input_missing",
            error=f"audio not found: {audio_path}",
        )

    pyres = _try_pyannote(audio_path, hf_token=hf_token)
    if pyres is not None and pyres.success:
        return pyres
    fallback = _single_speaker_fallback(audio_path)
    if pyres is not None and not pyres.success:
        fallback.warning = (
            fallback.warning or ""
        ) + f" | pyannote_error: {pyres.error or pyres.method}"
    return fallback


def _overlap_ms(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


# Stage 41 — offline dialogue roles when PyAnnote is off / single-speaker fallback.
SPEAKER_TURN_GAP_MS = 400
# Stage 42 — intra-segment mixed-speaker split (350–400 ms).
MIXED_SPEAKER_GAP_MS = 350
SPEAKER_A = "SPEAKER_A"
SPEAKER_B = "SPEAKER_B"
_SPEAKER_KEYS = (
    "speaker_id",
    "speaker",
    "speaker_uuid",
    "spk",
    "spk_id",
    "speaker_label",
)
_EMPTY_SPEAKER = frozenset(
    {"", "default", "none", "null", "unknown", "speaker_00", "speaker_0"}
)


def extract_speaker_label(seg: dict[str, Any] | None) -> str:
    """Read STT/diarization speaker from common keys. Empty if none."""
    if not isinstance(seg, dict):
        return ""
    for key in _SPEAKER_KEYS:
        raw = seg.get(key)
        if raw is None:
            continue
        val = str(raw).strip()
        if val and val.lower() not in _EMPTY_SPEAKER:
            return val
    return ""


def _normalize_role_id(label: str, *, seen: list[str] | None = None) -> str:
    raw = str(label or "").strip()
    if not raw:
        return SPEAKER_A
    up = raw.upper().replace("-", "_").replace(" ", "_")
    if up in (SPEAKER_A, "SPEAKER_00", "SPEAKER_0", "SPK_A", "A", "HOST", "0"):
        return SPEAKER_A
    if up in (SPEAKER_B, "SPEAKER_01", "SPEAKER_1", "SPK_B", "B", "GUEST", "1"):
        return SPEAKER_B
    if seen is not None:
        if raw not in seen:
            seen.append(raw)
        idx = seen.index(raw)
        return SPEAKER_A if idx == 0 else SPEAKER_B
    return SPEAKER_A if up.endswith("_00") or up.endswith("_0") else SPEAKER_B


def _seg_times(seg: dict[str, Any]) -> tuple[int, int]:
    try:
        s0 = int(seg.get("start_ms") or seg.get("start") or 0)
        s1 = int(seg.get("end_ms") or seg.get("end") or 0)
        if s1 <= s0:
            s1 = s0 + int(seg.get("duration_ms") or seg.get("slot_ms") or 0)
    except Exception:
        s0, s1 = 0, 0
    return s0, s1


def assign_speakers_heuristic(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """New speaker when pause gap > 400 ms AND turns alternate (A↔B).

    Offline fallback when diarization labels are missing. Dialogue gets at
    least SPEAKER_A + SPEAKER_B; a single continuous voice stays SPEAKER_A.
    """
    rows = [s for s in (segments or []) if isinstance(s, dict)]
    if not rows:
        return segments or []

    existing = [extract_speaker_label(s) for s in rows]
    labeled = [x for x in existing if x]
    unique_existing = []
    for lab in labeled:
        if lab not in unique_existing:
            unique_existing.append(lab)

    if len(unique_existing) >= 2:
        for seg, lab in zip(rows, existing):
            role = _normalize_role_id(lab or unique_existing[0], seen=unique_existing)
            seg["speaker"] = role
            seg["speaker_id"] = role
            seg["speaker_source"] = "stt_label"
        return segments

    if labeled and len(unique_existing) == 1:
        # One STT label for everyone — still try gap heuristic for dialogue.
        pass

    timed = []
    for i, seg in enumerate(rows):
        s0, s1 = _seg_times(seg)
        timed.append((i, s0, s1, seg))
    timed.sort(key=lambda x: (x[1], x[2], x[0]))

    turn_gaps = 0
    current = SPEAKER_A
    prev_end: int | None = None
    for _i, s0, s1, seg in timed:
        gap = 0 if prev_end is None else max(0, s0 - prev_end)
        if prev_end is not None and gap > SPEAKER_TURN_GAP_MS:
            current = SPEAKER_B if current == SPEAKER_A else SPEAKER_A
            turn_gaps += 1
        seg["speaker"] = current
        seg["speaker_id"] = current
        seg["speaker_source"] = "gap_heuristic"
        if s1 > 0:
            prev_end = s1

    roles = {str(s.get("speaker_id") or "") for s in rows}
    if turn_gaps == 0 and len(rows) >= 2:
        # No long pause: keep a single role (monologue).
        for seg in rows:
            seg["speaker"] = SPEAKER_A
            seg["speaker_id"] = SPEAKER_A
            seg["speaker_source"] = "single_speaker"
    elif SPEAKER_B not in roles and turn_gaps > 0:
        # Guarantee two dialogue roles after the first long gap.
        flipped = False
        prev_end = None
        for _i, s0, s1, seg in timed:
            gap = 0 if prev_end is None else max(0, s0 - prev_end)
            if not flipped and prev_end is not None and gap > SPEAKER_TURN_GAP_MS:
                seg["speaker"] = SPEAKER_B
                seg["speaker_id"] = SPEAKER_B
                flipped = True
            if s1 > 0:
                prev_end = s1
    return segments


def build_speaker_map(segments: list[dict[str, Any]] | None) -> dict[str, Any]:
    """{speaker_id: {segment_indices, count}} plus ordered role list."""
    roles: dict[str, dict[str, Any]] = {}
    for i, seg in enumerate(segments or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        sid = str(seg.get("speaker_id") or extract_speaker_label(seg) or SPEAKER_A)
        rec = roles.setdefault(
            sid, {"id": sid, "segment_indices": [], "count": 0}
        )
        rec["segment_indices"].append(int(seg.get("index", i)))
        rec["count"] += 1
    return {
        "roles": list(roles.values()),
        "speaker_ids": list(roles.keys()),
        "count": len(roles),
    }


def ensure_speaker_ids(
    segments: list[dict[str, Any]] | None,
    *,
    dia: DiarizationResult | None = None,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stamp speaker_id on every live segment. Uses diarization, else heuristic."""
    rows = list(segments or [])
    if dia is not None and dia.success and len(dia.speakers or []) >= 2:
        assign_speakers_to_segments(rows, dia)
        seen: list[str] = []
        for seg in rows:
            if not isinstance(seg, dict):
                continue
            raw = extract_speaker_label(seg) or SPEAKER_A
            role = _normalize_role_id(raw, seen=seen)
            seg["speaker"] = role
            seg["speaker_id"] = role
            seg.setdefault("speaker_source", "diarization")
    else:
        assign_speakers_heuristic(rows)
    try:
        split_rows = split_mixed_speaker_segments(rows, dia=dia, task_info=task_info)
        if split_rows is not rows:
            rows = split_rows
            if segments is not None:
                try:
                    segments[:] = split_rows
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("mixed-speaker split skipped: %s", exc)
    smap = build_speaker_map(rows)
    if task_info is not None:
        task_info["speaker_map"] = smap
        task_info["speaker_ids"] = list(smap.get("speaker_ids") or [])
    return smap


def _word_start_ms(word: dict[str, Any]) -> int:
    try:
        return int(word.get("start_ms") or word.get("start") or 0)
    except (TypeError, ValueError):
        return 0


def _word_end_ms(word: dict[str, Any]) -> int:
    try:
        end = int(word.get("end_ms") or word.get("end") or 0)
    except (TypeError, ValueError):
        end = 0
    if end <= 0:
        end = _word_start_ms(word)
    return end


def _word_speaker(word: dict[str, Any]) -> str:
    if not isinstance(word, dict):
        return ""
    for key in ("speaker_id", "speaker", "spk", "spk_id"):
        raw = str(word.get(key) or "").strip()
        if raw and raw.lower() not in _EMPTY_SPEAKER:
            return raw
    return ""


def _seg_words(seg: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("words", "word_timestamps", "source_words"):
        raw = seg.get(key)
        if isinstance(raw, list) and raw:
            return [w for w in raw if isinstance(w, dict)]
    wm = seg.get("source_word_map")
    if isinstance(wm, list) and wm:
        return [w for w in wm if isinstance(w, dict)]
    return []


def _seg_original_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("original_text")
        or seg.get("original")
        or seg.get("source_text")
        or seg.get("whisper_text")
        or seg.get("text")
        or ""
    ).strip()


def detect_text_dialogue_split(original: str) -> tuple[str, str] | None:
    """Split a glued two-person original (Hey man… + A dollar?)."""
    import re

    t = " ".join(str(original or "").split()).strip()
    if not t:
        return None
    m = re.match(
        r"(?is)^(hey\b(?:\s+(?:there'?s\s+my\s+big\s+(?:guy|man)|my\s+big\s+guy|man|dude|bro|guys?))?"
        r"[^.?!]{0,100}[.!?])\s+(.+)$",
        t,
    )
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        if right and (
            "?" in right
            or re.match(
                r"(?i)^(a\s+dollar|are\s+you|what|wait|seriously|shut\s+up)\b",
                right,
            )
        ):
            return left, right
    parts = re.split(r"(?<=[.!])\s+", t, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        if (
            re.search(r"(?i)\bhey\b", left)
            and "?" in right
            and 1 <= len(right.split()) <= 14
        ):
            return left, right
    return None


def _cut_from_words(words: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Return (word_index, cut_ms) when speaker labels or a turn gap appear."""
    if len(words) < 2:
        return None
    prev_spk = ""
    prev_end: int | None = None
    for i, w in enumerate(words):
        spk = _word_speaker(w)
        start = _word_start_ms(w)
        if prev_spk and spk and spk != prev_spk:
            return i, start
        if (
            prev_end is not None
            and start - prev_end >= MIXED_SPEAKER_GAP_MS
            and i >= 1
        ):
            prev_tok = str(words[i - 1].get("word") or words[i - 1].get("text") or "")
            if prev_tok.rstrip().endswith((".", "!", "?", "…")) or spk:
                return i, start
        if spk:
            prev_spk = spk
        end = _word_end_ms(w)
        if end > 0:
            prev_end = end
    return None


def _cut_from_diarization(
    seg: dict[str, Any],
    dia: DiarizationResult | None,
) -> int | None:
    if dia is None or not dia.success or not dia.turns:
        return None
    s0, s1 = _seg_times(seg)
    if s1 <= s0:
        return None
    covered: list[tuple[int, str]] = []
    for t in dia.turns:
        ov = _overlap_ms(s0, s1, t.start_ms, t.end_ms)
        if ov >= 180:
            covered.append((max(s0, t.start_ms), t.speaker))
    speakers = {spk for _st, spk in covered}
    if len(speakers) < 2:
        return None
    covered.sort()
    for i in range(1, len(covered)):
        if covered[i][1] != covered[0][1]:
            return int(covered[i][0])
    return None


def _split_text_at_word_index(text: str, words: list[dict[str, Any]], idx: int) -> tuple[str, str]:
    left_parts: list[str] = []
    right_parts: list[str] = []
    for i, w in enumerate(words):
        tok = str(w.get("word") or w.get("text") or "").strip()
        if not tok:
            continue
        if i < idx:
            left_parts.append(tok)
        else:
            right_parts.append(tok)
    left = " ".join(left_parts).strip()
    right = " ".join(right_parts).strip()
    if left and right:
        return left, right
    t = " ".join(str(text or "").split())
    mid = max(1, (len(t) * idx) // max(len(words), 1))
    return t[:mid].strip(), t[mid:].strip()


def split_mixed_speaker_segments(
    segments: list[dict[str, Any]] | None,
    *,
    dia: DiarizationResult | None = None,
    task_info: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Stage 42 A1: 1 segment = 1 speaker = 1 replica. Archive + mint on split."""
    rows = [s for s in (segments or []) if isinstance(s, dict)]
    if not rows:
        return list(segments or [])

    out: list[dict[str, Any]] = []
    split_count = 0
    for seg in rows:
        if seg.get("merged_into") is not None or seg.get("archived"):
            out.append(seg)
            continue
        original = _seg_original_text(seg)
        words = _seg_words(seg)
        cut_ms: int | None = None
        left = ""
        right = ""
        cut = _cut_from_words(words) if words else None
        if cut is not None:
            widx, cut_ms = cut
            left, right = _split_text_at_word_index(original, words, widx)
            if words:
                spk_l = _word_speaker(words[0]) or SPEAKER_A
                spk_r = _word_speaker(words[widx]) or SPEAKER_B
            else:
                spk_l, spk_r = SPEAKER_A, SPEAKER_B
        else:
            dia_cut = _cut_from_diarization(seg, dia)
            text_pair = detect_text_dialogue_split(original)
            if dia_cut is not None and text_pair:
                left, right = text_pair
                cut_ms = dia_cut
                spk_l, spk_r = SPEAKER_A, SPEAKER_B
            elif text_pair:
                left, right = text_pair
                s0, s1 = _seg_times(seg)
                span = max(1, s1 - s0)
                cut_ms = s0 + int(span * (len(left) / max(len(original), 1)))
                spk_l, spk_r = SPEAKER_A, SPEAKER_B
            elif dia_cut is not None:
                s0, s1 = _seg_times(seg)
                ratio = (dia_cut - s0) / max(1, s1 - s0)
                mid = max(1, int(len(original) * ratio))
                left, right = original[:mid].strip(), original[mid:].strip()
                cut_ms = dia_cut
                spk_l, spk_r = SPEAKER_A, SPEAKER_B
            else:
                out.append(seg)
                continue

        if not left or not right or left == original:
            out.append(seg)
            continue

        s0, s1 = _seg_times(seg)
        cut_ms = int(cut_ms or (s0 + max(1, (s1 - s0) // 2)))
        cut_ms = min(max(cut_ms, s0 + 80), s1 - 80) if s1 - s0 > 200 else cut_ms
        child_a = dict(seg)
        child_b = dict(seg)
        for key in ("file", "tts_file_path", "fitted_file", "final_tts_text", "tts_text"):
            child_a.pop(key, None)
            child_b.pop(key, None)
        for child, text, st, en, spk in (
            (child_a, left, s0, cut_ms, spk_l),
            (child_b, right, cut_ms, s1, spk_r),
        ):
            child["text"] = text
            child["plain_text"] = text
            child["original"] = text
            child["original_text"] = text
            child["source_text"] = text
            child["whisper_text"] = text
            child["start_ms"] = int(st)
            child["end_ms"] = int(en)
            child["slot_ms"] = max(0, int(en) - int(st))
            child["speaker"] = spk
            child["speaker_id"] = spk
            child["speaker_source"] = "mixed_split"
        try:
            from engines.pipeline_integrity.identity_guard import reissue_split_children

            _archived, fresh = reissue_split_children(seg, [child_a, child_b])
            if len(fresh) >= 2:
                out.extend(fresh)
                split_count += 1
                continue
        except Exception as exc:
            logger.debug("mixed-speaker split reissue skipped: %s", exc)
        out.append(seg)

    if split_count and task_info is not None:
        task_info["mixed_speaker_splits"] = int(
            task_info.get("mixed_speaker_splits") or 0
        ) + split_count
        try:
            smap = build_speaker_map(out)
            task_info["speaker_map"] = smap
            task_info["speaker_ids"] = list(smap.get("speaker_ids") or [])
        except Exception:
            pass
        logger.info("[diarization] mixed-speaker splits=%d", split_count)
    # Re-index live rows
    live_i = 0
    for seg in out:
        if not isinstance(seg, dict) or seg.get("archived") or seg.get("merged_into") is not None:
            continue
        seg["index"] = live_i
        live_i += 1
    return out


def assign_speakers_to_segments(
    segments: list[dict[str, Any]],
    dia: DiarizationResult,
) -> list[dict[str, Any]]:
    """Stamp seg["speaker"] / speaker_id and confidence using overlap-max.

    Mutates ``segments`` in place and also returns it for chaining.
    """
    if not segments or not dia or not dia.turns:
        for s in segments or []:
            s.setdefault("speaker", "SPEAKER_00")
            s.setdefault("speaker_id", s.get("speaker") or "SPEAKER_00")
            s.setdefault("speaker_confidence", 0.0)
        return segments or []

    for seg in segments:
        try:
            s0 = int(seg.get("start_ms") or 0)
            s1 = int(seg.get("end_ms") or (s0 + int(seg.get("duration_ms") or 0)))
        except Exception:
            s0, s1 = 0, 0
        if s1 <= s0:
            seg.setdefault("speaker", "SPEAKER_00")
            seg.setdefault("speaker_id", seg.get("speaker") or "SPEAKER_00")
            seg.setdefault("speaker_confidence", 0.0)
            continue
        per_speaker: dict[str, int] = {}
        for t in dia.turns:
            ov = _overlap_ms(s0, s1, t.start_ms, t.end_ms)
            if ov > 0:
                per_speaker[t.speaker] = per_speaker.get(t.speaker, 0) + ov
        if per_speaker:
            best = max(per_speaker.items(), key=lambda kv: kv[1])
            total_ov = sum(per_speaker.values())
            seg["speaker"] = best[0]
            seg["speaker_id"] = best[0]
            seg["speaker_confidence"] = round(best[1] / max(1, total_ov), 4)
        else:
            seg["speaker"] = "SPEAKER_00"
            seg["speaker_id"] = "SPEAKER_00"
            seg["speaker_confidence"] = 0.0
    return segments


def _ffmpeg_bin() -> str | None:
    try:
        from engines.ffmpeg_paths import find_ffmpeg

        return find_ffmpeg()
    except Exception:
        return None


def extract_speaker_reference_clip(
    audio_path: str,
    dia: DiarizationResult,
    speaker: str,
    out_wav: str,
    *,
    min_ms: int = 5000,
    max_ms: int = 12000,
) -> dict[str, Any]:
    """Concat the longest turns of ``speaker`` into a single mono 16k WAV.

    Returns diagnostics dict with ``ok``, ``file``, ``duration_ms``, ``turns_used``.
    """
    ffmpeg = _ffmpeg_bin()
    diag: dict[str, Any] = {"speaker": speaker, "ok": False, "file": None, "duration_ms": 0}
    if not ffmpeg or not Path(audio_path).is_file():
        diag["error"] = "ffmpeg_or_audio_missing"
        return diag
    turns = sorted(
        [t for t in dia.turns if t.speaker == speaker and t.duration_ms() > 300],
        key=lambda t: t.duration_ms(),
        reverse=True,
    )
    if not turns:
        diag["error"] = "no_turns"
        return diag

    picked: list[SpeakerTurn] = []
    total = 0
    for t in turns:
        picked.append(t)
        total += t.duration_ms()
        if total >= min_ms:
            break
    if not picked:
        diag["error"] = "empty_pick"
        return diag

    parts_dir = Path(out_wav).parent / "_spk_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_paths: list[str] = []
    for i, t in enumerate(picked):
        p = parts_dir / f"{Path(out_wav).stem}_{i:02d}.wav"
        duration = min(max_ms, t.duration_ms()) / 1000.0
        try:
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{t.start_ms / 1000.0:.3f}",
                    "-t", f"{duration:.3f}",
                    "-i", audio_path,
                    "-ac", "1", "-ar", "16000",
                    "-acodec", "pcm_s16le",
                    str(p),
                ],
                capture_output=True, text=True, timeout=120,
            )
            if p.is_file() and p.stat().st_size > 0:
                part_paths.append(str(p))
        except Exception as exc:
            logger.debug("[diarization] part cut failed: %s", exc)
    if not part_paths:
        diag["error"] = "cut_failed"
        return diag

    if len(part_paths) == 1:
        try:
            import shutil as _sh

            _sh.copy2(part_paths[0], out_wav)
        except Exception as exc:
            diag["error"] = f"copy_failed: {exc}"
            return diag
    else:
        concat_list = parts_dir / f"{Path(out_wav).stem}_list.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in part_paths), encoding="utf-8"
        )
        try:
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-ac", "1", "-ar", "16000",
                    "-acodec", "pcm_s16le",
                    out_wav,
                ],
                capture_output=True, text=True, timeout=120,
            )
        except Exception as exc:
            diag["error"] = f"concat_failed: {exc}"
            return diag

    if Path(out_wav).is_file():
        try:
            from pydub import AudioSegment as _AS

            diag["duration_ms"] = int(len(_AS.from_file(out_wav)))
        except Exception:
            diag["duration_ms"] = int(total)
        diag["ok"] = True
        diag["file"] = str(Path(out_wav).resolve())
        diag["turns_used"] = len(part_paths)
    else:
        diag["error"] = "out_missing"
    return diag


def build_speaker_profiles(
    audio_path: str,
    dia: DiarizationResult,
    profiles_dir: str,
    *,
    min_ms: int = 5000,
    max_ms: int = 12000,
) -> dict[str, dict[str, Any]]:
    """Extract a reference clip for each detected speaker.

    Returns ``{speaker_id: {ok, file, duration_ms, ...}}``.
    """
    profiles_dir_path = Path(profiles_dir)
    profiles_dir_path.mkdir(parents=True, exist_ok=True)
    profiles: dict[str, dict[str, Any]] = {}
    if not dia or not dia.success or not dia.speakers:
        return profiles
    for sp in dia.speakers:
        out_wav = profiles_dir_path / f"speaker_{sp}.wav"
        profiles[sp] = extract_speaker_reference_clip(
            audio_path, dia, sp, str(out_wav), min_ms=min_ms, max_ms=max_ms
        )
    return profiles
