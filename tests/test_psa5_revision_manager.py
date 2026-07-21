"""PSA5 — RevisionManager: new UUID on text change, sidecar, flag=0."""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.pipeline_integrity.exceptions import RevisionManagerError
from engines.pipeline_integrity.identity_guard import assert_consistent, bind
from engines.pipeline_integrity.psa_flags import (
    VM_FLAG_IDENTITY_GUARD,
    VM_FLAG_REVISION_MANAGER,
)
from engines.pipeline_integrity.revision_manager import (
    REVISION_UUID_FIELDS,
    assert_no_inplace_text_mutate,
    assert_revision_chain,
    assert_sidecar_matches_segment,
    ensure_revision_uuids,
    forbid_inplace_text_assign,
    note_text_change,
    read_wav_sidecar,
    write_wav_sidecar,
)


@pytest.fixture
def rev_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "1")
    monkeypatch.delenv("VM_REVISION_MANAGER", raising=False)
    yield


@pytest.fixture
def rev_off(monkeypatch):
    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "0")
    monkeypatch.delenv("VM_REVISION_MANAGER", raising=False)
    yield


@pytest.fixture
def ig_and_rev_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "1")
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    yield


def _sid(n: int = 1) -> str:
    return f"c{n:031x}"


def test_psa5_fields_stamped(rev_on):
    seg = {"segment_id": _sid(1), "plain_text": "Hello"}
    ids = ensure_revision_uuids(seg)
    for field in REVISION_UUID_FIELDS:
        assert field in ids and ids[field]
        assert seg.get(field)


def test_psa5_text_change_mints_new_uuid(rev_on):
    seg = {"segment_id": _sid(2), "plain_text": "Old text"}
    ensure_revision_uuids(seg)
    old_ad = seg["adaptation_uuid"]
    old_tr = seg["translation_uuid"]
    note_text_change(seg, "Brand new adaptation", kind="adaptation")
    assert seg["adaptation_uuid"] != old_ad
    assert seg["plain_text"] == "Brand new adaptation"
    assert seg.get("revision_text_hash")
    note_text_change(seg, "Fresh translation", kind="translation")
    assert seg["translation_uuid"] != old_tr


def test_psa5_inplace_raises(rev_on):
    seg = {"segment_id": _sid(3)}
    note_text_change(seg, "Stable first", kind="translation")
    # Silent in-place mutate (no note_text_change)
    with pytest.raises(RevisionManagerError) as exc:
        forbid_inplace_text_assign(seg, "Hacked in-place text")
    assert "in-place" in str(exc.value).lower()

    seg["plain_text"] = "Hacked without revision"
    with pytest.raises(RevisionManagerError):
        assert_no_inplace_text_mutate(seg, force=True)


def test_psa5_sidecar_mismatch_fails(rev_on, tmp_path: Path):
    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"RIFF....")
    seg = {
        "segment_id": _sid(4),
        "tts_file_path": str(wav),
    }
    note_text_change(seg, "Spoken line", kind="translation")
    from engines.pipeline_integrity.revision_manager import ensure_tts_uuid

    ensure_tts_uuid(seg, force_new=True)
    path = write_wav_sidecar(wav, seg, force=True)
    assert path is not None and path.is_file()
    data = read_wav_sidecar(wav)
    assert data["tts_uuid"] == seg["tts_uuid"]
    assert data["translation_uuid"] == seg["translation_uuid"]

    # Corrupt segment uuid vs sidecar
    seg["tts_uuid"] = "f" * 32
    with pytest.raises(RevisionManagerError) as exc:
        assert_sidecar_matches_segment(seg, audio_path=wav, force=True)
    assert "tts_uuid" in str(exc.value).lower()


def test_psa5_identity_guard_checks_revision(ig_and_rev_on, tmp_path: Path):
    wav = tmp_path / "ig.wav"
    wav.write_bytes(b"RIFF")
    seg = {
        "segment_id": _sid(5),
        "tts_file_path": str(wav),
    }
    note_text_change(seg, "OK line", kind="translation")
    seg["final_tts_text"] = "OK line"
    from engines.pipeline_integrity.revision_manager import ensure_tts_uuid

    ensure_tts_uuid(seg, force_new=True)
    write_wav_sidecar(wav, seg, force=True)
    bind(seg, text="OK line", audio_path=str(wav), stage="post_tts", allow_rebind=True)
    report = assert_consistent([seg], stage="psa5")
    assert report["ok"] is True

    seg["translation_uuid"] = "e" * 32
    with pytest.raises(RevisionManagerError):
        assert_sidecar_matches_segment(seg, audio_path=wav, force=True)


def test_psa5_flag_off_legacy(rev_off):
    seg = {"segment_id": _sid(6), "plain_text": "A"}
    forbid_inplace_text_assign(seg, "B")  # no raise
    seg["plain_text"] = "B"
    assert_no_inplace_text_mutate(seg)  # no raise
    assert write_wav_sidecar("x.wav", seg) is None
    report = assert_revision_chain(seg)
    assert report["enabled"] is False
    # Legacy note_text_change still updates text
    note_text_change(seg, "C", kind="adaptation")
    assert seg["plain_text"] == "C"
