"""PSA2 — IdentityGuard (Pipeline Stability v2).

segment_id is UUID-only. Bind text revision/hash + audio_path per UUID.
assert_consistent after TTS / regen / resegment / slot_fit.
Remap only via UUID map (never list-index alone).
Flag: VM_FLAG_IDENTITY_GUARD (default OFF → legacy no-op).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from engines.pipeline_integrity.exceptions import (
    IdentityMismatchError,
    PipelineIdentityError,
)

logger = logging.getLogger("tubedub.pipeline_integrity.identity_guard")

__all__ = [
    "IdentityMismatchError",
    "apply_engine_text_results",
    "assert_consistent",
    "bind",
    "bind_after_tts",
    "remap_by_uuid",
    "resolve_row_by_identity",
    "run_identity_guard",
    "text_content_hash",
    "verify_identity_chain",
]

_BINDING_KEY = "identity_binding"


def _sid(seg: dict[str, Any]) -> str:
    return str(seg.get("segment_id") or seg.get("segment_uuid") or "").strip()


def _is_index_like_id(sid: str) -> bool:
    """True for list-index masquerading as id ('0'..'999999'), not hex UUIDs."""
    s = str(sid or "").strip()
    if not s:
        return True
    # Pure short decimals used historically as row indices
    if s.isdigit() and len(s) <= 8:
        return True
    return False


def _text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("plain_text")
        or seg.get("translation_text")
        or seg.get("translated_text")
        or seg.get("final_text")
        or seg.get("text")
        or ""
    ).strip()


def _tts_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("final_tts_text")
        or seg.get("tts_text")
        or ""
    ).strip()


def _wav_ref(seg: dict[str, Any]) -> str:
    return str(
        seg.get("tts_file_path")
        or seg.get("file")
        or seg.get("fitted_file")
        or ""
    ).strip()


def _text_revision(seg: dict[str, Any]) -> str:
    return str(
        seg.get("adaptation_uuid")
        or seg.get("text_revision_uuid")
        or seg.get("translation_uuid")
        or ""
    ).strip()


def text_content_hash(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip().lower()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _flag_on(*, force: bool = False) -> bool:
    if force:
        return True
    from engines.pipeline_integrity.v2_gates import identity_guard_enabled

    return bool(identity_guard_enabled())


def _shadow_mode(*, force: bool = False) -> bool:
    """Report-only: log violations, do not raise. force=True always enforces."""
    if force:
        return False
    from engines.pipeline_integrity.v2_gates import identity_guard_enforce_enabled

    return not bool(identity_guard_enforce_enabled())


def _shadow_report(
    exc: PipelineIdentityError,
    *,
    stage: str,
    segments_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    details = dict(getattr(exc, "details", None) or {})
    sid = str(details.get("segment_id") or "").strip()
    if sid and segments_data:
        for seg in segments_data:
            if isinstance(seg, dict) and _sid(seg) == sid:
                seg["identity_mismatch"] = True
                seg["identity_shift"] = True
                seg["identity_guard_violation"] = {
                    "message": str(exc),
                    "code": getattr(exc, "code", "identity_mismatch"),
                    "details": details,
                    "stage": stage,
                }
                break
    logger.warning(
        "[IdentityGuard] SHADOW violation stage=%s %s details=%s",
        stage,
        exc,
        details,
    )
    return {
        "enabled": True,
        "ok": False,
        "stage": stage,
        "report_only": True,
        "mismatches": 1,
        "violations": [
            {
                "message": str(exc),
                "code": getattr(exc, "code", "identity_mismatch"),
                "details": details,
            }
        ],
    }


def resolve_row_by_identity(
    segments_data: list[dict[str, Any]],
    *,
    segment_id: str = "",
    index: int | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve a row by UUID first; index only if the result has no UUID."""
    sid = str(segment_id or "").strip()
    if sid and not _is_index_like_id(sid):
        for row in segments_data or []:
            if isinstance(row, dict) and _sid(row) == sid:
                return row, "segment_id"
    if index is not None:
        try:
            i = int(index)
        except (TypeError, ValueError):
            i = -1
        if 0 <= i < len(segments_data or []) and isinstance(segments_data[i], dict):
            return segments_data[i], "index"
    return None, "miss"


def apply_engine_text_results(
    segments_data: list[dict[str, Any]],
    results: list[Any],
    *,
    text_fields: tuple[str, ...] = (
        "text",
        "plain_text",
        "translation_text",
        "final_text",
        "tts_text",
    ),
) -> dict[str, Any]:
    """Write result.output_text onto the matching segment_id row.

    Index fallback only when the result lacks a UUID. Shuffled results
    with segment_id must not bleed onto the wrong row.
    """
    stats: dict[str, Any] = {
        "applied_by_id": 0,
        "applied_by_index": 0,
        "skipped": 0,
        "warnings": [],
    }
    for r in results or []:
        sid = str(getattr(r, "segment_id", "") or "").strip()
        text = str(getattr(r, "output_text", "") or "").strip()
        idx = getattr(r, "index", None)
        has_uuid = bool(sid) and not _is_index_like_id(sid)
        if has_uuid:
            target, how = resolve_row_by_identity(
                segments_data, segment_id=sid, index=None
            )
        else:
            target, how = resolve_row_by_identity(
                segments_data, segment_id="", index=idx
            )
            if how == "index":
                logger.warning(
                    "IdentityGuard: engine result missing segment_id; "
                    "index fallback index=%s",
                    idx,
                )
                stats["warnings"].append(
                    {"index": idx, "reason": "missing_segment_id"}
                )
        if target is None or not text:
            stats["skipped"] += 1
            continue
        if how == "index":
            stats["applied_by_index"] += 1
        else:
            stats["applied_by_id"] += 1
        for key in text_fields:
            target[key] = text
    return stats


def _bind_strict(
    seg: dict[str, Any],
    *,
    text: str | None = None,
    text_revision: str | None = None,
    audio_path: str | None = None,
    stage: str = "bind",
    allow_rebind: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Bind segment_id → text_revision/hash (+ optional audio_path).

    Rebinding a different text/audio after TTS is forbidden unless
    ``allow_rebind=True`` (intentional regen) or flag is OFF.
    """
    if not _flag_on(force=force):
        return {"enabled": False, "noop": True, "stage": stage}

    if not isinstance(seg, dict):
        raise IdentityMismatchError(
            "IdentityGuard.bind: segment must be a dict",
            stage=stage,
        )
    sid = _sid(seg)
    if not sid:
        raise IdentityMismatchError(
            "IdentityGuard.bind: missing segment_id",
            stage=stage,
        )
    if _is_index_like_id(sid):
        raise IdentityMismatchError(
            f"IdentityGuard.bind: segment_id must be UUID, got index-like '{sid}'",
            stage=stage,
            details={"segment_id": sid},
        )

    bound_text = str(text if text is not None else _text(seg)).strip()
    thash = text_content_hash(bound_text)
    rev = str(text_revision if text_revision is not None else _text_revision(seg)).strip()
    if not rev and thash:
        rev = f"hash:{thash}"
    audio = str(audio_path if audio_path is not None else _wav_ref(seg)).strip()
    # Bare filenames write sidecars into CWD (diag 8c9850ef *.mp3.vm_rev.json).
    # Prefer the segment's absolute TTS path when the caller passed a basename.
    abs_ref = _wav_ref(seg)
    if audio and abs_ref and abs_ref != audio:
        try:
            from pathlib import Path

            a = Path(audio)
            r = Path(abs_ref)
            if (not a.is_absolute()) and r.is_absolute() and r.name == a.name:
                audio = str(r)
        except Exception:
            pass

    prev = seg.get(_BINDING_KEY) if isinstance(seg.get(_BINDING_KEY), dict) else {}
    prev_tts_bound = bool(prev.get("tts_bound"))
    if prev_tts_bound and not allow_rebind:
        prev_hash = str(prev.get("text_hash") or "")
        prev_audio = str(prev.get("audio_path") or "")
        text_changed = bool(thash and prev_hash and thash != prev_hash)
        audio_changed = bool(audio and prev_audio and audio != prev_audio)
        if text_changed or audio_changed:
            raise IdentityMismatchError(
                "IdentityGuard: identity rebind after TTS forbidden "
                "(use allow_rebind for intentional regen)",
                stage=stage,
                details={
                    "segment_id": sid,
                    "prev_text_hash": prev_hash,
                    "new_text_hash": thash,
                    "prev_audio_path": prev_audio,
                    "new_audio_path": audio,
                },
            )

    binding = {
        "segment_id": sid,
        "text_hash": thash,
        "text_revision": rev,
        "audio_path": audio,
        "bound_at_stage": stage,
        "tts_bound": bool(audio) or prev_tts_bound,
    }
    seg[_BINDING_KEY] = binding
    seg["owned_text_segment_id"] = sid
    if thash:
        seg["identity_text_hash"] = thash
    if rev:
        seg["identity_text_revision"] = rev
    if audio:
        seg["wav_segment_id"] = sid
        meta = seg.get("tts_meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["segment_id"] = sid
        meta["text_hash"] = thash
        seg["tts_meta"] = meta
        # PSA5 — stamp revision uuids into meta when RevisionManager is ON
        try:
            from engines.pipeline_integrity.revision_manager import (
                ensure_revision_uuids,
                write_wav_sidecar,
            )
            from engines.pipeline_integrity.v2_gates import revision_manager_enabled

            if revision_manager_enabled():
                ensure_revision_uuids(seg)
                meta["source_segment_uuid"] = seg.get("source_segment_uuid")
                meta["translation_uuid"] = seg.get("translation_uuid")
                meta["adaptation_uuid"] = seg.get("adaptation_uuid")
                meta["tts_uuid"] = seg.get("tts_uuid")
                seg["tts_meta"] = meta
                write_wav_sidecar(audio, seg)
        except Exception:
            pass
    return {"enabled": True, "noop": False, "stage": stage, "binding": dict(binding)}


def bind(
    seg: dict[str, Any],
    *,
    text: str | None = None,
    text_revision: str | None = None,
    audio_path: str | None = None,
    stage: str = "bind",
    allow_rebind: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    try:
        return _bind_strict(
            seg,
            text=text,
            text_revision=text_revision,
            audio_path=audio_path,
            stage=stage,
            allow_rebind=allow_rebind,
            force=force,
        )
    except PipelineIdentityError as exc:
        if _shadow_mode(force=force):
            return _shadow_report(exc, stage=stage, segments_data=[seg] if isinstance(seg, dict) else None)
        raise


def _bind_after_tts_strict(
    seg: dict[str, Any],
    *,
    tts_text: str,
    audio_path: str | None,
    stage: str = "post_tts",
    allow_rebind: bool = False,
    force: bool = False,
    segments_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Orchestration helper: bind spoken text + wav to segment_id after TTS.

    Does not reject legitimate merge-head / adapted text; foreign-neighbor
    contamination is detected when ``segments_data`` is provided.
    """
    if not _flag_on(force=force):
        return {"enabled": False, "noop": True, "stage": stage}

    spoken = str(tts_text or "").strip()
    owned = _text(seg)
    sid = _sid(seg)

    if spoken and segments_data:
        spoken_hash = text_content_hash(spoken)
        for other in segments_data:
            if not isinstance(other, dict) or other is seg:
                continue
            if other.get("merged_into") is not None:
                continue
            other_sid = _sid(other)
            if not other_sid or other_sid == sid:
                continue
            other_owned = _text(other)
            if (
                other_owned
                and spoken_hash == text_content_hash(other_owned)
                and (not owned or text_content_hash(owned) != spoken_hash)
            ):
                raise IdentityMismatchError(
                    "IdentityGuard: foreign TTS text belongs to another segment_id",
                    stage=stage,
                    details={
                        "segment_id": sid,
                        "donor_segment_id": other_sid,
                        "tts_preview": spoken[:100],
                    },
                )

    if spoken:
        seg["final_tts_text"] = spoken
        seg["tts_text"] = spoken
    return _bind_strict(
        seg,
        text=spoken or owned,
        audio_path=audio_path,
        stage=stage,
        allow_rebind=allow_rebind,
        force=force,
    )


def bind_after_tts(
    seg: dict[str, Any],
    *,
    tts_text: str,
    audio_path: str | None,
    stage: str = "post_tts",
    allow_rebind: bool = False,
    force: bool = False,
    segments_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Orchestration helper: bind spoken text + wav to segment_id after TTS."""
    try:
        return _bind_after_tts_strict(
            seg,
            tts_text=tts_text,
            audio_path=audio_path,
            stage=stage,
            allow_rebind=allow_rebind,
            force=force,
            segments_data=segments_data,
        )
    except PipelineIdentityError as exc:
        if _shadow_mode(force=force):
            rows = list(segments_data or [])
            if isinstance(seg, dict) and seg not in rows:
                rows = [seg] + rows
            return _shadow_report(exc, stage=stage, segments_data=rows)
        raise


def remap_by_uuid(
    segments_data: list[dict[str, Any]],
    uuid_map: dict[str, str],
    payloads_by_uuid: dict[str, dict[str, Any]] | None = None,
    *,
    stage: str = "remap",
    force: bool = False,
) -> list[dict[str, Any]]:
    """Remap / merge payloads keyed ONLY by segment_id UUID.

    ``uuid_map``: old_uuid → new_uuid (identity continuity).
    Index-only remaps without a UUID map are rejected when the flag is ON.
    """
    if not _flag_on(force=force):
        return list(segments_data or [])

    if not isinstance(uuid_map, dict) or not uuid_map:
        raise IdentityMismatchError(
            "IdentityGuard.remap_by_uuid: uuid_map required "
            "(index-only remap forbidden)",
            stage=stage,
        )

    by_id = {_sid(s): s for s in (segments_data or []) if isinstance(s, dict) and _sid(s)}
    payloads = payloads_by_uuid or {}

    for old_id, new_id in uuid_map.items():
        old_k = str(old_id or "").strip()
        new_k = str(new_id or "").strip()
        if not old_k or not new_k:
            raise IdentityMismatchError(
                "IdentityGuard.remap_by_uuid: empty uuid in map",
                stage=stage,
                details={"old": old_k, "new": new_k},
            )
        if _is_index_like_id(old_k) or _is_index_like_id(new_k):
            raise IdentityMismatchError(
                "IdentityGuard.remap_by_uuid: index-like ids forbidden",
                stage=stage,
                details={"old": old_k, "new": new_k},
            )
        target = by_id.get(new_k) or by_id.get(old_k)
        if target is None:
            continue
        if _sid(target) == old_k and new_k != old_k:
            target["segment_id"] = new_k
            target["segment_uuid"] = new_k
            by_id[new_k] = target
        payload = payloads.get(old_k) or payloads.get(new_k)
        if isinstance(payload, dict):
            for key, val in payload.items():
                if key in ("segment_id", "segment_uuid"):
                    continue
                target[key] = val
        bind(target, stage=stage, allow_rebind=True, force=force)

    return list(segments_data or [])


def assert_consistent(
    segments_data: list[dict[str, Any]],
    *,
    stage: str,
    require_wav: bool = False,
    require_adaptation_uuid: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Fail-fast consistency check (post TTS / regen / resegment / slot_fit)."""
    return verify_identity_chain(
        segments_data,
        stage=stage,
        require_wav=require_wav,
        require_adaptation_uuid=require_adaptation_uuid,
        force=force,
    )


def _verify_identity_chain_strict(
    segments_data: list[dict[str, Any]],
    *,
    stage: str,
    require_wav: bool = False,
    require_adaptation_uuid: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Fail-fast IdentityGuard. Raises IdentityMismatchError on violation.
    Returns a compact report when OK.
    """
    if not _flag_on(force=force):
        return {"enabled": False, "stage": stage, "ok": True}

    if not segments_data:
        raise IdentityMismatchError(
            "IdentityGuard: segments_data empty",
            stage=stage,
        )

    seen: set[str] = set()
    wav_owners: dict[str, str] = {}
    text_hash_owners: dict[str, str] = {}
    checked = 0

    for i, seg in enumerate(segments_data):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None:
            continue
        sid = _sid(seg)
        if not sid:
            raise IdentityMismatchError(
                f"IdentityGuard: missing segment_id at row {i}",
                stage=stage,
                details={"index": i},
            )
        if _is_index_like_id(sid):
            raise IdentityMismatchError(
                f"IdentityGuard: segment_id must be UUID, got index-like '{sid}'",
                stage=stage,
                details={"index": i, "segment_id": sid},
            )
        if sid in seen:
            raise IdentityMismatchError(
                f"IdentityGuard: duplicate segment_id {sid}",
                stage=stage,
                details={"segment_id": sid, "index": i},
            )
        seen.add(sid)

        owned_text = _text(seg)
        owned = str(seg.get("owned_text_segment_id") or sid).strip()
        if owned_text and owned and owned != sid:
            raise IdentityMismatchError(
                "IdentityGuard: text ownership mismatch",
                stage=stage,
                details={
                    "segment_id": sid,
                    "owned_text_segment_id": owned,
                    "index": i,
                },
            )

        tts_spoken = _tts_text(seg)

        binding = seg.get(_BINDING_KEY) if isinstance(seg.get(_BINDING_KEY), dict) else None
        if binding:
            b_sid = str(binding.get("segment_id") or "").strip()
            if b_sid and b_sid != sid:
                raise IdentityMismatchError(
                    "IdentityGuard: binding segment_id mismatch",
                    stage=stage,
                    details={"segment_id": sid, "binding_segment_id": b_sid},
                )
            b_hash = str(binding.get("text_hash") or "")
            current_hash = text_content_hash(tts_spoken or owned_text)
            if b_hash and current_hash and b_hash != current_hash:
                raise IdentityMismatchError(
                    "IdentityGuard: bound text_hash mismatch",
                    stage=stage,
                    details={
                        "segment_id": sid,
                        "bound_hash": b_hash,
                        "current_hash": current_hash,
                    },
                )
            b_audio = str(binding.get("audio_path") or "").strip()
            wav = _wav_ref(seg)
            if b_audio and wav and b_audio != wav and binding.get("tts_bound"):
                # fitted_file may differ from original tts path — allow basename match
                from pathlib import Path

                if Path(b_audio).name != Path(wav).name:
                    raise IdentityMismatchError(
                        "IdentityGuard: bound audio_path mismatch",
                        stage=stage,
                        details={
                            "segment_id": sid,
                            "bound_audio": b_audio,
                            "current_audio": wav,
                        },
                    )

        tr_uuid = str(seg.get("translation_uuid") or "").strip()
        if owned_text and not tr_uuid:
            try:
                from engines.pipeline_integrity.uuid_chain import (
                    ensure_translation_uuid,
                )

                ensure_translation_uuid(seg)
            except Exception:
                pass

        ad_uuid = str(seg.get("adaptation_uuid") or "").strip()
        if require_adaptation_uuid and owned_text and not ad_uuid:
            raise IdentityMismatchError(
                "IdentityGuard: missing adaptation_uuid",
                stage=stage,
                details={"segment_id": sid},
            )

        wav = _wav_ref(seg)
        if require_wav and not wav:
            raise IdentityMismatchError(
                "IdentityGuard: missing wav after TTS stage",
                stage=stage,
                details={"segment_id": sid},
            )
        if wav:
            prev = wav_owners.get(wav)
            if prev and prev != sid:
                raise IdentityMismatchError(
                    "IdentityGuard: wav bound to multiple segment_id "
                    "(foreign wav write forbidden)",
                    stage=stage,
                    details={"wav": wav, "owners": [prev, sid]},
                )
            wav_owners[wav] = sid
            meta_sid = str(
                (seg.get("tts_meta") or {}).get("segment_id")
                or seg.get("wav_segment_id")
                or ""
            ).strip()
            if meta_sid and meta_sid != sid:
                raise IdentityMismatchError(
                    "IdentityGuard: wav metadata segment_id mismatch",
                    stage=stage,
                    details={
                        "segment_id": sid,
                        "wav_segment_id": meta_sid,
                    },
                )

        # PSA5 — IdentityGuard verifies revision UUIDs + sidecar
        try:
            from engines.pipeline_integrity.exceptions import RevisionManagerError
            from engines.pipeline_integrity.revision_manager import (
                assert_revision_chain,
            )
            from engines.pipeline_integrity.v2_gates import revision_manager_enabled

            if revision_manager_enabled():
                assert_revision_chain(seg, stage=stage)
        except RevisionManagerError:
            raise
        except Exception:
            pass

        # Detect cross-segment contamination: same spoken hash owned by two UUIDs
        # when texts are non-empty and distinct segment rows claim identical TTS
        # that equals another row's owned text (classic ba6ec shift pattern).
        if tts_spoken:
            th = text_content_hash(tts_spoken)
            if th:
                other = text_hash_owners.get(th)
                if other and other != sid:
                    # Only flag when this row's owned text differs (true shift)
                    if owned_text and text_content_hash(owned_text) != th:
                        raise IdentityMismatchError(
                            "IdentityGuard: foreign TTS text already bound "
                            "to another segment_id",
                            stage=stage,
                            details={
                                "segment_id": sid,
                                "other_segment_id": other,
                                "text_hash": th,
                            },
                        )
                else:
                    text_hash_owners[th] = sid

        seg["owned_text_segment_id"] = sid
        checked += 1

    # Second pass: neighbor meaning landing (ba6ec): A's final_tts == B's owned
    # and A's owned differs. Also catch plain owned≠tts when not a merge head.
    by_owned_hash: dict[str, str] = {}
    merge_heads: set[str] = set()
    for i, seg in enumerate(segments_data):
        if not isinstance(seg, dict):
            continue
        sid = _sid(seg)
        if seg.get("merged_into") is not None:
            # TZ Phase 10: resolve merge head by UUID only — no list-index fallback.
            head_id = str(seg.get("merged_into_id") or "").strip()
            if head_id:
                merge_heads.add(head_id)
            continue
        ot = _text(seg)
        if ot and sid:
            by_owned_hash[text_content_hash(ot)] = sid

    for i, seg in enumerate(segments_data):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        sid = _sid(seg)
        spoken = _tts_text(seg)
        owned = _text(seg)
        if not spoken or not owned:
            continue
        sh = text_content_hash(spoken)
        oh = text_content_hash(owned)
        if sh == oh:
            continue
        donor = by_owned_hash.get(sh)
        if donor and donor != sid:
            raise IdentityMismatchError(
                "IdentityGuard: identity shift — TTS text belongs to "
                f"neighbor segment_id {donor}",
                stage=stage,
                details={
                    "segment_id": sid,
                    "index": i,
                    "donor_segment_id": donor,
                    "tts_preview": spoken[:100],
                    "owned_preview": owned[:100],
                },
            )
        # Non-merge rows: owned translation and final TTS must stay aligned.
        if sid not in merge_heads:
            raise IdentityMismatchError(
                "IdentityGuard: identity shift — final_tts_text/tts_text "
                "does not match owned translated text for segment_id",
                stage=stage,
                details={
                    "segment_id": sid,
                    "index": i,
                    "owned_preview": owned[:100],
                    "tts_preview": spoken[:100],
                },
            )

    # PSA3 — Immutable Segment Contract (works with IdentityGuard)
    from engines.pipeline_integrity.immutable_segment import (
        assert_no_text_move_or_swap,
    )

    assert_no_text_move_or_swap(segments_data, stage=stage, force=force)

    report = {
        "enabled": True,
        "stage": stage,
        "ok": True,
        "checked": checked,
        "unique_ids": len(seen),
    }
    logger.info(
        "[IdentityGuard] ok stage=%s checked=%d",
        stage,
        checked,
    )
    return report


def verify_identity_chain(
    segments_data: list[dict[str, Any]],
    *,
    stage: str,
    require_wav: bool = False,
    require_adaptation_uuid: bool = False,
    force: bool = False,
    report_only: bool | None = None,
) -> dict[str, Any]:
    """IdentityGuard check. Raises on violation unless shadow/report-only."""
    shadow = bool(report_only) if report_only is not None else _shadow_mode(force=force)
    try:
        return _verify_identity_chain_strict(
            segments_data,
            stage=stage,
            require_wav=require_wav,
            require_adaptation_uuid=require_adaptation_uuid,
            force=force,
        )
    except PipelineIdentityError as exc:
        if shadow:
            return _shadow_report(exc, stage=stage, segments_data=segments_data)
        raise


def run_identity_guard(
    segments_data: list[dict[str, Any]],
    *,
    stage: str,
    task_info: dict[str, Any] | None = None,
    require_wav: bool = False,
) -> dict[str, Any]:
    """Public entry — stores report on task_info when provided."""
    report = assert_consistent(
        segments_data,
        stage=stage,
        require_wav=require_wav,
    )
    if task_info is not None:
        hist = list(task_info.get("identity_guard_log") or [])
        hist.append(report)
        task_info["identity_guard_log"] = hist[-40:]
        if report.get("ok") is False or report.get("violations"):
            viol = list(task_info.get("identity_guard_violations") or [])
            viol.extend(report.get("violations") or [])
            task_info["identity_guard_violations"] = viol[-80:]
            task_info["identity_guard_shadow"] = bool(report.get("report_only"))
    return report


def archive_and_reissue_ids(
    old_segments: list[dict[str, Any]],
    new_texts: list[str],
    new_timing: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """
    On boundary change, archive old UUIDs and mint new ones.
    Returns (archived_rows, fresh_segments, uuid_map old→new via content order
    only when a single old maps; otherwise uuid_map is empty and callers must
    supply explicit UUID map for payload remap).
    """
    from engines.pipeline_integrity.segment import new_segment_id
    from engines.pipeline_integrity.uuid_chain import ensure_all_uuids

    archived: list[dict[str, Any]] = []
    old_ids: list[str] = []
    for seg in old_segments:
        if not isinstance(seg, dict):
            continue
        sid = _sid(seg)
        old_ids.append(sid)
        archived.append(
            {
                "segment_id": sid,
                "archived": True,
                "text": _text(seg),
                "start_ms": seg.get("start_ms"),
                "end_ms": seg.get("end_ms"),
                "translation_uuid": seg.get("translation_uuid"),
                "adaptation_uuid": seg.get("adaptation_uuid"),
                "tts_uuid": seg.get("tts_uuid"),
            }
        )

    fresh: list[dict[str, Any]] = []
    new_ids: list[str] = []
    n = min(len(new_texts), len(new_timing)) if new_timing else len(new_texts)
    live_old = [s for s in old_segments if isinstance(s, dict)]
    copy_en_src = n == len(live_old)
    for i in range(n):
        text = str(new_texts[i] or "").strip()
        if not text:
            continue
        tm = new_timing[i] if new_timing and i < len(new_timing) else {}
        if isinstance(tm, dict):
            s, e = int(tm.get("start", 0)), int(tm.get("end", 0))
        elif isinstance(tm, (list, tuple)) and len(tm) >= 2:
            s, e = int(tm[0]), int(tm[1])
        else:
            s, e = 0, 0
        sid = new_segment_id()
        new_ids.append(sid)
        seg = {
            "segment_id": sid,
            "segment_uuid": sid,
            "text": text,
            "plain_text": text,
            "start_ms": s,
            "end_ms": e,
            "slot_ms": max(0, e - s),
            "owned_text_segment_id": sid,
            "reissued_from_resegment": True,
        }
        # Zip 955dd5ec: 1:1 reissue dropped original English so remt
        # failed with no_remt_or_empty_source.
        if copy_en_src and i < len(live_old):
            src_row = live_old[i]
            for key in (
                "original",
                "original_text",
                "whisper_text",
                "source_text",
                "source_lang",
            ):
                val = src_row.get(key)
                if val not in (None, ""):
                    seg[key] = val
        ensure_all_uuids(seg)
        if _flag_on():
            bind(seg, text=text, stage="resegment", allow_rebind=True)
        fresh.append(seg)

    # UUID map only when 1:1 reissue (never invent index-only multi maps)
    uuid_map: dict[str, str] = {}
    if len(old_ids) == 1 and len(new_ids) == 1 and old_ids[0] and new_ids[0]:
        uuid_map[old_ids[0]] = new_ids[0]

    return archived, fresh, uuid_map


_CHILD_FILE_KEYS = (
    "file",
    "tts_file_path",
    "fitted_file",
    "runtime_registry_path",
    "oss_segs_path",
)


def reissue_split_children(
    parent: dict[str, Any],
    children: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Archive parent UUID and mint NEW ids for every split child.

    Children never inherit the parent TTS file. Translation/adaptation/TTS
    must rerun (``needs_retts``). Unique text is the caller's duty
    (``unique_text_ok``); this helper only refuses a copied parent file.
    """
    from engines.pipeline_integrity.segment import new_segment_id
    from engines.pipeline_integrity.uuid_chain import ensure_all_uuids, ensure_tts_uuid

    parent_sid = _sid(parent)
    archived = {
        "segment_id": parent_sid,
        "archived": True,
        "text": _text(parent),
        "start_ms": parent.get("start_ms"),
        "end_ms": parent.get("end_ms"),
        "translation_uuid": parent.get("translation_uuid"),
        "adaptation_uuid": parent.get("adaptation_uuid"),
        "tts_uuid": parent.get("tts_uuid"),
        "parent_segment_id": None,
    }
    if isinstance(parent, dict):
        parent["archived"] = True
        parent["reissued_to"] = []

    fresh: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        row = dict(child)
        for key in _CHILD_FILE_KEYS:
            row.pop(key, None)
        sid = new_segment_id()
        row["segment_id"] = sid
        row["segment_uuid"] = sid
        row["owned_text_segment_id"] = sid
        row["parent_segment_id"] = parent_sid
        row["reissued_from"] = [parent_sid] if parent_sid else []
        row["split_from_segment_id"] = parent_sid
        row["needs_retts"] = True
        row["archived"] = False
        row.pop("merged_into", None)
        row.pop("merged_into_id", None)
        ensure_all_uuids(row)
        ensure_tts_uuid(row, force_new=True)
        if _flag_on():
            try:
                bind(row, text=_text(row), stage="resegment_split", allow_rebind=True)
            except Exception:
                pass
        fresh.append(row)
        if isinstance(parent, dict):
            parent.setdefault("reissued_to", []).append(sid)

    return archived, fresh
