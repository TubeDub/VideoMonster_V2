"""Canonical TTS-stage segment fields (read-only plain_text after Translation)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TTS_ALLOWED_MUTATIONS: frozenset[str] = frozenset(
    {
        "tts_text",
        "tts_file_path",
        "playback_duration",
        "status",
        # Stage 6 speedup stamps (parallel + disk cache)
        "tts_cache_hit",
        "tts_synth_rate",
        "tts_synth_pitch",
        # Stage 20 — backend / voice metadata
        "tts_backend",
        "tts_engine",
        "tts_voice",
        "tts_language",
        "tts_sample_rate",
        "cyrillic_ratio",
        "file",
        "resolved_path",
        # Stage 22 — Mykyta / tts_uk voice controls
        "tts_rate",
        "tts_pitch",
        "tts_volume",
        "tts_length_scale",
        # Stage 25 §1 — UK hard-lock (`resolve_uk_tts`) may override the
        # per-speaker `voice` field to the canonical tts_uk short id
        # (mykyta / tetiana / lada) or the safe Edge uk-UA-* fallback when
        # `voice` came in as `uk_UA-*-high` (Piper) or a forbidden
        # cross-locale id. Guarded by StageSnapshotIntegrityError otherwise.
        "voice",
        "voice_override_reason",
        # Stage 26 §3 — honest post-synth stamps: when tts_uk retry fails
        # and we transparently fall back to Edge uk-UA-*Neural, record why
        # + what the caller had originally requested so the JSON does not
        # lie about backend/voice identity.
        "tts_fallback_reason",
        "tts_engine_requested",
        "tts_voice_requested",
        # Stage 26 §5 — duration-control diagnostics ("length_scale" /
        # "atempo" / "text_expand" / "text_shorten" / "soft_pad") when
        # |slot_ms − tts_ms| > 250ms so callers can audit the strategy.
        "duration_control_used",
        # Stage 26 §1.3 — presence / audit stamps (mirror `audio_presence`).
        "audio_padded",
        "silence_pad",
        "pad_reason",
        "audio_exists",
        "audio_size_bytes",
        "needs_re_tts",
        # TubeDub TZ — IdentityGuard bind_after_tts + RevisionManager sidecar
        # (diag 8c9850ef: STAGE_SNAPSHOT_INTEGRITY on identity_binding at tts).
        # TTS is the stage that completes the bind: audio_path / tts_bound /
        # bound_at_stage. Frozen identity keys (segment_id, text_hash) stay
        # the same; only the audio half of the binding is filled in.
        "identity_binding",
        "tts_meta",
        "revision_text_hash",
        "wav_segment_id",
        "owned_text_segment_id",
        "identity_text_hash",
        "identity_text_revision",
        # Stage 40 — bind_after_tts / RevisionManager complete the spoken bind.
        "final_tts_text",
        "source_segment_uuid",
        "translation_uuid",
        "adaptation_uuid",
        "assigned_voice",
        "engine_id",
        # Nested bind keys (diag 8c9850ef) — never abort if they surface top-level.
        "audio_path",
        "tts_bound",
        "bound_at_stage",
        "sidecar_path",
    }
)


def _stage15_restore_full_meaning(seg: dict[str, Any], text: str) -> str:
    """If text lost >15% words vs Raw MT — restore full meaning (Final=TTS)."""
    try:
        from engines.text_slot_fit import prefer_full_meaning_text

        raw_mt = str(
            seg.get("raw_translation")
            or seg.get("raw_mt")
            or ""
        )
        text2, restored = prefer_full_meaning_text(text, raw_mt)
        if restored and text2:
            for key in (
                "final_tts_text",
                "final_text",
                "plain_text",
                "tts_text",
                "text",
            ):
                if key in seg or key == "final_tts_text":
                    seg[key] = text2
            return text2
    except Exception:
        pass
    return text


def resolve_segment_text_for_tts(seg: dict[str, Any]) -> str:
    """Stage 18: voice only Final (after Stage 15 restore / Stage 16 repairs).

    Never prefer Raw MT / Naturalized / grammar buffers when Final exists.
    """
    if seg.get("tts_blocked") or seg.get("skip_tts"):
        return ""
    if "FAIL" in str(seg.get("tqe_status") or "").upper() and not str(
        seg.get("approved_text") or ""
    ).strip():
        return ""
    # Final-only when any Final field is present.
    final_only = str(
        seg.get("final_tts_text")
        or seg.get("approved_text")
        or seg.get("final_text")
        or ""
    ).strip()
    if final_only:
        restored = _stage15_restore_full_meaning(seg, final_only)
        # Stage 18: never replace good uk Final with latin/raw garbage.
        try:
            from engines.tts_lang_lock import is_uk_tts_text_ok

            if is_uk_tts_text_ok(final_only) and not is_uk_tts_text_ok(restored):
                return final_only
        except Exception:
            pass
        return restored

    from engines.translation_validation import (
        is_shared_mt_blob_reclaim,
        resolve_post_quality_text,
    )

    owned = resolve_post_quality_text(seg)
    text = owned or str(
        seg.get("plain_text")
        or seg.get("translated_text")
        or seg.get("text")
        or ""
    ).strip()
    # Do not fall back to raw_translation / naturalized_text when absent Final —
    # those are MT buffers, not spoken Final.
    final_owned = str(seg.get("translated_text") or "").strip()
    if final_owned and text and is_shared_mt_blob_reclaim(
        final_owned,
        text,
        raw_mt=str(seg.get("raw_translation") or ""),
    ):
        text = final_owned
    if not text:
        return ""
    # Last line of defense: never voice source-script leak / meaning collapse
    try:
        from engines.mt.cross_script_guard import meaning_collapse, source_script_leak
        from engines.translation_validation import texts_equivalent_for_ownership

        source = str(
            seg.get("original")
            or seg.get("original_text")
            or seg.get("whisper_text")
            or seg.get("source_text")
            or ""
        )
        tgt = str(seg.get("target_lang") or seg.get("lang") or "")
        if source and source_script_leak(source, text):
            return ""
        if source and meaning_collapse(source, text, target_lang=tgt or None):
            if not (
                final_owned and texts_equivalent_for_ownership(text, final_owned)
            ):
                return ""
    except Exception:
        pass
    try:
        from engines.semantic_meaning import restore_terminal_close

        original = str(
            seg.get("original")
            or seg.get("original_text")
            or seg.get("whisper_text")
            or seg.get("source_text")
            or ""
        )
        text = restore_terminal_close(text, original=original)
    except Exception:
        pass
    # Stage 14b: never send glossary placeholder garbage to TTS.
    try:
        from engines.mt.glossary_en_uk import (
            contains_glossary_garbage,
            finalize_mt_text,
            strip_glossary_placeholders,
        )

        text = finalize_mt_text("en", "uk", text)
        if contains_glossary_garbage(text):
            logger = __import__("logging").getLogger("tubedub.tts_fields")
            logger.error("[glossary] TTS text still dirty after finalize: %r", text[:120])
            text = strip_glossary_placeholders(text)
    except Exception:
        pass
    return _stage15_restore_full_meaning(seg, text)


def resolve_tts_input_text(group: dict[str, Any]) -> str:
    """
    Text sent to the TTS engine for a group.
    Prefer locked final_tts_text / plain_text — never a divergent buffer.
    """
    try:
        from engines.tts_text_authority import resolve_group_spoken_text

        locked = resolve_group_spoken_text(group)
        if locked:
            return locked
    except Exception:
        pass
    plain = str(group.get("plain_text") or "").strip()
    ssml_or_plain = str(group.get("text") or "").strip()
    text = plain if plain else ssml_or_plain
    if text.lstrip().startswith("<speak"):
        text = re.sub(r"<[^>]+>", " ", text).strip()
    return text


def apply_tts_synthesis_result(
    seg: dict[str, Any],
    *,
    tts_text: str,
    tts_file_path: str | None,
    playback_duration: int | None = None,
    status: str = "generated",
) -> None:
    """Mutate only TTS-contract fields on a segment row."""
    seg["tts_text"] = tts_text
    abs_path = tts_file_path
    if tts_file_path:
        try:
            p = Path(str(tts_file_path))
            if p.is_file():
                abs_path = str(p.resolve())
        except OSError:
            abs_path = tts_file_path
    seg["tts_file_path"] = abs_path
    if abs_path:
        seg["file"] = abs_path
        seg["resolved_path"] = abs_path
    if playback_duration is not None:
        seg["playback_duration"] = int(playback_duration)
        try:
            if int(playback_duration) > 0:
                seg["tts_ms"] = int(playback_duration)
        except (TypeError, ValueError):
            pass
    seg["status"] = status


def apply_tts_group_merge_links(
    segments_data: list[dict[str, Any]],
    tts_groups: list[dict[str, Any]],
) -> None:
    """Establish merge pointers before TTS snapshot (not a TTS-stage mutation)."""
    for group in tts_groups:
        indices = group.get("indices") or []
        if len(indices) <= 1:
            continue
        head = int(indices[0])
        if head >= len(segments_data):
            continue
        head_sid = segments_data[head].get("segment_id")
        for rest_idx in indices[1:]:
            ri = int(rest_idx)
            if ri >= len(segments_data):
                continue
            segments_data[ri]["merged_into"] = head
            if head_sid:
                segments_data[ri]["merged_into_id"] = head_sid


def mark_merged_tts_children(
    segments_data: list[dict[str, Any]],
    indices: list[int],
) -> None:
    """Mark non-head group members after head synthesis (TTS-contract fields only)."""
    if len(indices) <= 1:
        return
    for rest_idx in indices[1:]:
        ri = int(rest_idx)
        if ri >= len(segments_data):
            continue
        apply_tts_synthesis_result(
            segments_data[ri],
            tts_text="",
            tts_file_path=None,
            status="merged",
        )


def sync_tts_legacy_fields(segments_data: list[dict[str, Any]]) -> None:
    """Map canonical TTS fields to legacy keys for slot_fit / studio (after TTS guard)."""
    from engines.dub_engine_v2.adaptation_decision import stamp_need_adaptation_gate

    for i, seg in enumerate(segments_data):
        tfp = seg.get("tts_file_path")
        if tfp:
            name = Path(str(tfp)).name
            seg["file"] = name
        elif seg.get("status") in ("merged", "empty", "failed"):
            seg["file"] = None
        pd = seg.get("playback_duration")
        if pd is not None:
            seg["tts_ms"] = int(pd)
        st = seg.get("status")
        if st:
            seg["tts_status"] = st
        # Recognize need_adaptation as soon as measured TTS duration exists.
        if int(seg.get("tts_ms") or seg.get("playback_duration") or 0) > 0:
            stamp_need_adaptation_gate(seg, index=i, source="post_tts_sync")


def measure_playback_duration_ms(audio_path: str | Path | None) -> int:
    if not audio_path:
        return 0
    try:
        from pydub import AudioSegment

        return len(AudioSegment.from_file(str(audio_path)))
    except Exception:
        return 0


def resolve_segment_audio_ref(seg: dict[str, Any]) -> str | None:
    """
    Effective segment audio filename for integrity checks and mux.

    Prefer seg['file'] — slot_fit and studio stages update the working copy here.
    Fall back to tts_file_path when file is unset (merged/empty rows).
    """
    legacy = seg.get("file")
    if legacy:
        return Path(str(legacy)).name
    tfp = seg.get("tts_file_path")
    if tfp:
        return Path(str(tfp)).name
    return None
