"""PSA5 — RevisionManager (Pipeline Stability v2).

Fields: source_segment_uuid, translation_uuid, adaptation_uuid, tts_uuid.
Any text change → NEW uuid (in-place mutate forbidden when flag ON).
Wav sidecar/meta stores tts_uuid + translation_uuid for IdentityGuard.
Flag: VM_FLAG_REVISION_MANAGER (default OFF → legacy).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.exceptions import RevisionManagerError

logger = logging.getLogger("tubedub.pipeline_integrity.revision_manager")

__all__ = [
    "REVISION_UUID_FIELDS",
    "RevisionManagerError",
    "assert_no_inplace_text_mutate",
    "assert_revision_chain",
    "assert_sidecar_matches_segment",
    "ensure_adaptation_uuid",
    "ensure_revision_uuids",
    "ensure_source_segment_uuid",
    "ensure_tts_uuid",
    "note_text_change",
    "read_wav_sidecar",
    "sidecar_path_for",
    "stamp_text_revision",
    "write_wav_sidecar",
]

REVISION_UUID_FIELDS: tuple[str, ...] = (
    "source_segment_uuid",
    "translation_uuid",
    "adaptation_uuid",
    "tts_uuid",
)

_TEXT_HASH_KEY = "revision_text_hash"
_SIDECAR_SUFFIX = ".vm_rev.json"


def _new_uuid() -> str:
    return uuid.uuid4().hex


def _flag_on(*, force: bool = False) -> bool:
    if force:
        return True
    from engines.pipeline_integrity.v2_gates import revision_manager_enabled

    return bool(revision_manager_enabled())


def _text_of(seg: dict[str, Any]) -> str:
    return str(
        seg.get("plain_text")
        or seg.get("final_text")
        or seg.get("translation_text")
        or seg.get("translated_text")
        or seg.get("text")
        or ""
    ).strip()


def text_revision_hash(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def ensure_source_segment_uuid(seg: dict[str, Any]) -> str:
    """Canonical source segment UUID (aligned with segment_id)."""
    existing = str(seg.get("source_segment_uuid") or "").strip()
    if existing:
        return existing
    sid = str(seg.get("segment_id") or seg.get("segment_uuid") or "").strip()
    if not sid:
        sid = _new_uuid()
        seg["segment_id"] = sid
    seg["source_segment_uuid"] = sid
    if not str(seg.get("segment_uuid") or "").strip():
        seg["segment_uuid"] = sid
    return sid


def ensure_adaptation_uuid(seg: dict[str, Any], *, force_new: bool = False) -> str:
    if not force_new:
        existing = str(seg.get("adaptation_uuid") or "").strip()
        if existing:
            return existing
    fresh = _new_uuid()
    seg["adaptation_uuid"] = fresh
    return fresh


def ensure_tts_uuid(seg: dict[str, Any], *, force_new: bool = False) -> str:
    if not force_new:
        existing = str(seg.get("tts_uuid") or "").strip()
        if existing:
            return existing
    fresh = _new_uuid()
    seg["tts_uuid"] = fresh
    return fresh


def ensure_revision_uuids(seg: dict[str, Any]) -> dict[str, str]:
    """Stamp PSA5 revision UUID fields (create-if-missing, no text change)."""
    from engines.pipeline_integrity.uuid_chain import ensure_translation_uuid

    out = {
        "source_segment_uuid": ensure_source_segment_uuid(seg),
        "translation_uuid": ensure_translation_uuid(seg),
        "adaptation_uuid": ensure_adaptation_uuid(seg),
        "tts_uuid": ensure_tts_uuid(seg),
    }
    text = _text_of(seg)
    if text and not str(seg.get(_TEXT_HASH_KEY) or "").strip():
        seg[_TEXT_HASH_KEY] = text_revision_hash(text)
    return out


def stamp_text_revision(
    seg: dict[str, Any],
    *,
    kind: str,
    text: str,
    previous_uuid: str | None = None,
    force: bool = False,
) -> str:
    """
    Create a new revision for a text change.
    kind: translation | adaptation | naturalizer | meaning_fit | tts_prep | source
    """
    text = str(text or "").strip()
    rev_id = _new_uuid()
    ensure_source_segment_uuid(seg)

    if _flag_on(force=force):
        chain = list(seg.get("revision_chain") or [])
        chain.append(
            {
                "revision_uuid": rev_id,
                "kind": kind,
                "previous": previous_uuid
                or seg.get("adaptation_uuid")
                or seg.get("translation_uuid"),
                "text_hash": text_revision_hash(text)[:16] if text else "",
            }
        )
        seg["revision_chain"] = chain[-30:]
        if kind in ("adaptation", "naturalizer", "meaning_fit", "semantic_shortening"):
            seg["adaptation_uuid"] = rev_id
        elif kind in ("translation", "source"):
            seg["translation_uuid"] = rev_id
            ensure_adaptation_uuid(seg, force_new=True)
        elif kind == "tts_prep":
            seg["tts_uuid"] = rev_id
        else:
            # Unknown kind → adaptation revision
            seg["adaptation_uuid"] = rev_id
        seg[_TEXT_HASH_KEY] = text_revision_hash(text)
    else:
        if kind in ("adaptation", "naturalizer", "meaning_fit"):
            ensure_adaptation_uuid(seg, force_new=True)
        elif kind == "tts_prep":
            ensure_tts_uuid(seg, force_new=True)

    seg["text_revision_uuid"] = rev_id
    return rev_id


def note_text_change(
    seg: dict[str, Any],
    new_text: str,
    *,
    kind: str = "adaptation",
    force: bool = False,
) -> str | None:
    """Authorized text rewrite: always mints a NEW revision uuid when text changes."""
    old = _text_of(seg)
    new = str(new_text or "").strip()
    if not new or new == old:
        return None
    rev = stamp_text_revision(seg, kind=kind, text=new, force=force)
    seg["plain_text"] = new
    seg["text"] = new
    if kind != "tts_prep":
        seg["final_text"] = new
    if kind == "translation":
        seg["translation_text"] = new
        seg["translated_text"] = new
    return rev


def assert_no_inplace_text_mutate(
    seg: dict[str, Any],
    *,
    stage: str = "revision_manager",
    force: bool = False,
) -> None:
    """Raise if text content changed without a matching revision hash/uuid update."""
    if not _flag_on(force=force):
        return
    if not isinstance(seg, dict) or seg.get("archived") or seg.get("merged_into") is not None:
        return

    text = _text_of(seg)
    if not text:
        return

    stamped = str(seg.get(_TEXT_HASH_KEY) or "").strip()
    current = text_revision_hash(text)
    if stamped and stamped != current:
        raise RevisionManagerError(
            "RevisionManager: in-place text mutate forbidden "
            "(text changed without new revision uuid)",
            stage=stage,
            details={
                "segment_id": str(seg.get("segment_id") or ""),
                "stamped_hash": stamped,
                "current_hash": current,
                "translation_uuid": seg.get("translation_uuid"),
                "adaptation_uuid": seg.get("adaptation_uuid"),
            },
        )

    # Text present but no revision uuid at all after stamp expectation
    tr = str(seg.get("translation_uuid") or "").strip()
    ad = str(seg.get("adaptation_uuid") or "").strip()
    if not tr and not ad and stamped:
        raise RevisionManagerError(
            "RevisionManager: missing translation_uuid/adaptation_uuid",
            stage=stage,
            details={"segment_id": str(seg.get("segment_id") or "")},
        )


def forbid_inplace_text_assign(
    seg: dict[str, Any],
    new_text: str,
    *,
    stage: str = "revision_manager",
    force: bool = False,
) -> None:
    """Explicit API: assigning text without note_text_change raises when flag ON."""
    if not _flag_on(force=force):
        return
    old = _text_of(seg)
    new = str(new_text or "").strip()
    if new and new != old:
        raise RevisionManagerError(
            "RevisionManager: in-place text assign forbidden — use note_text_change()",
            stage=stage,
            details={
                "segment_id": str(seg.get("segment_id") or ""),
                "old_preview": old[:80],
                "new_preview": new[:80],
            },
        )


def sidecar_path_for(audio_path: str | Path) -> Path:
    p = Path(str(audio_path))
    return p.with_suffix(p.suffix + _SIDECAR_SUFFIX) if p.suffix else Path(str(p) + _SIDECAR_SUFFIX)


def _absolute_audio_path(
    audio_path: str | Path | None,
    seg: dict[str, Any] | None = None,
) -> str | None:
    """Prefer an absolute session path so sidecars never land in CWD."""
    raw = str(audio_path or "").strip()
    if raw:
        p = Path(raw)
        if p.is_absolute():
            return str(p)
    if isinstance(seg, dict):
        for key in ("tts_file_path", "resolved_path", "file"):
            cand = str(seg.get(key) or "").strip()
            if cand and Path(cand).is_absolute():
                return cand
    return raw or None


def write_wav_sidecar(
    audio_path: str | Path | None,
    seg: dict[str, Any],
    *,
    force: bool = False,
) -> Path | None:
    """Persist tts_uuid + translation_uuid (+ source) next to wav/mp3."""
    if not audio_path:
        return None
    if not _flag_on(force=force):
        return None

    ensure_revision_uuids(seg)
    ensure_tts_uuid(seg, force_new=False)
    resolved = _absolute_audio_path(audio_path, seg)
    if not resolved or not Path(resolved).is_absolute():
        logger.warning(
            "[RevisionManager] sidecar skipped — refuse CWD write for relative %s",
            audio_path,
        )
        return None
    audio_path = resolved
    path = sidecar_path_for(audio_path)
    payload = {
        "source_segment_uuid": str(seg.get("source_segment_uuid") or ""),
        "segment_id": str(seg.get("segment_id") or ""),
        "translation_uuid": str(seg.get("translation_uuid") or ""),
        "adaptation_uuid": str(seg.get("adaptation_uuid") or ""),
        "tts_uuid": str(seg.get("tts_uuid") or ""),
        "revision_text_hash": str(seg.get(_TEXT_HASH_KEY) or ""),
    }
    # Also mirror into in-memory tts_meta for IdentityGuard
    meta = seg.get("tts_meta") if isinstance(seg.get("tts_meta"), dict) else {}
    meta.update(
        {
            "segment_id": payload["segment_id"],
            "source_segment_uuid": payload["source_segment_uuid"],
            "translation_uuid": payload["translation_uuid"],
            "adaptation_uuid": payload["adaptation_uuid"],
            "tts_uuid": payload["tts_uuid"],
            "sidecar_path": str(path),
        }
    )
    seg["tts_meta"] = meta

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("[RevisionManager] sidecar write failed: %s", exc)
        return None
    return path


def read_wav_sidecar(audio_path: str | Path | None) -> dict[str, Any] | None:
    if not audio_path:
        return None
    path = sidecar_path_for(audio_path)
    if not path.is_file():
        # Fall back to bare .meta.json if present
        alt = Path(str(audio_path) + ".meta.json")
        path = alt if alt.is_file() else path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def assert_sidecar_matches_segment(
    seg: dict[str, Any],
    *,
    audio_path: str | Path | None = None,
    stage: str = "revision_sidecar",
    force: bool = False,
) -> dict[str, Any]:
    """Fail when wav sidecar tts_uuid/translation_uuid disagree with segment."""
    if not _flag_on(force=force):
        return {"enabled": False, "ok": True}

    wav = str(
        audio_path
        or seg.get("tts_file_path")
        or seg.get("file")
        or seg.get("fitted_file")
        or ""
    ).strip()
    meta = seg.get("tts_meta") if isinstance(seg.get("tts_meta"), dict) else {}
    sidecar = read_wav_sidecar(wav) if wav else None
    if sidecar is None and meta:
        sidecar = {
            "tts_uuid": meta.get("tts_uuid"),
            "translation_uuid": meta.get("translation_uuid"),
            "source_segment_uuid": meta.get("source_segment_uuid"),
            "segment_id": meta.get("segment_id"),
        }

    if not sidecar:
        # No sidecar yet — OK until audio is bound
        if not wav:
            return {"enabled": True, "ok": True, "skipped": True}
        raise RevisionManagerError(
            "RevisionManager: wav sidecar missing tts_uuid/translation_uuid",
            stage=stage,
            details={
                "segment_id": str(seg.get("segment_id") or ""),
                "audio_path": wav,
            },
        )

    seg_tts = str(seg.get("tts_uuid") or "").strip()
    seg_tr = str(seg.get("translation_uuid") or "").strip()
    sc_tts = str(sidecar.get("tts_uuid") or "").strip()
    sc_tr = str(sidecar.get("translation_uuid") or "").strip()

    if sc_tts and seg_tts and sc_tts != seg_tts:
        raise RevisionManagerError(
            "RevisionManager: sidecar tts_uuid mismatch",
            stage=stage,
            details={
                "segment_id": str(seg.get("segment_id") or ""),
                "segment_tts_uuid": seg_tts,
                "sidecar_tts_uuid": sc_tts,
            },
        )
    if sc_tr and seg_tr and sc_tr != seg_tr:
        raise RevisionManagerError(
            "RevisionManager: sidecar translation_uuid mismatch",
            stage=stage,
            details={
                "segment_id": str(seg.get("segment_id") or ""),
                "segment_translation_uuid": seg_tr,
                "sidecar_translation_uuid": sc_tr,
            },
        )

    sc_sid = str(
        sidecar.get("source_segment_uuid") or sidecar.get("segment_id") or ""
    ).strip()
    seg_sid = str(
        seg.get("source_segment_uuid") or seg.get("segment_id") or ""
    ).strip()
    if sc_sid and seg_sid and sc_sid != seg_sid:
        raise RevisionManagerError(
            "RevisionManager: sidecar source_segment_uuid mismatch",
            stage=stage,
            details={
                "segment_id": seg_sid,
                "sidecar_segment_id": sc_sid,
            },
        )

    return {"enabled": True, "ok": True, "sidecar": sidecar}


def assert_revision_chain(
    seg: dict[str, Any],
    *,
    stage: str = "revision_manager",
    require_sidecar: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """IdentityGuard helper: revision uuids + optional sidecar check."""
    if not _flag_on(force=force):
        return {"enabled": False, "ok": True}

    ensure_source_segment_uuid(seg)
    assert_no_inplace_text_mutate(seg, stage=stage, force=force)

    for field in ("translation_uuid", "adaptation_uuid"):
        # Soft-ensure when text exists
        if _text_of(seg) and not str(seg.get(field) or "").strip():
            if field == "translation_uuid":
                from engines.pipeline_integrity.uuid_chain import ensure_translation_uuid

                ensure_translation_uuid(seg)
            else:
                ensure_adaptation_uuid(seg)

    wav = str(
        seg.get("tts_file_path") or seg.get("file") or seg.get("fitted_file") or ""
    ).strip()
    if wav or require_sidecar:
        if wav and not str(seg.get("tts_uuid") or "").strip():
            ensure_tts_uuid(seg)
        assert_sidecar_matches_segment(
            seg, audio_path=wav or None, stage=stage, force=force
        )

    return {
        "enabled": True,
        "ok": True,
        "source_segment_uuid": seg.get("source_segment_uuid"),
        "translation_uuid": seg.get("translation_uuid"),
        "adaptation_uuid": seg.get("adaptation_uuid"),
        "tts_uuid": seg.get("tts_uuid"),
    }
