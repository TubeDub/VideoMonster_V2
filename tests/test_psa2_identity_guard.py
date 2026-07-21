"""PSA2 — IdentityGuard: bind / assert_consistent / UUID remap / flag=0."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.exceptions import IdentityMismatchError
from engines.pipeline_integrity.identity_guard import (
    assert_consistent,
    bind,
    bind_after_tts,
    remap_by_uuid,
    run_identity_guard,
    text_content_hash,
    verify_identity_chain,
)
from engines.pipeline_integrity.psa_flags import VM_FLAG_IDENTITY_GUARD

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ba6ec_compact.json"


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    monkeypatch.delenv("VM_IDENTITY_GUARD", raising=False)
    yield


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv(VM_FLAG_IDENTITY_GUARD, raising=False)
    monkeypatch.delenv("VM_IDENTITY_GUARD", raising=False)
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "0")
    yield


def _uuid(n: int = 0) -> str:
    # Include hex letters so ids are never mistaken for short decimal indices.
    return f"a{n:031x}"


def test_psa2_flag_off_legacy_noop(flag_off):
    seg = {
        "segment_id": _uuid(1),
        "plain_text": "A",
        "final_tts_text": "B",  # would be shift if flag ON
    }
    report = verify_identity_chain([seg], stage="legacy")
    assert report["enabled"] is False
    assert report["ok"] is True
    bind_res = bind(seg, stage="legacy")
    assert bind_res["noop"] is True
    # remap without map is also no-op when OFF
    out = remap_by_uuid([seg], {}, stage="legacy")
    assert out == [seg]


def test_psa2_identity_rebind_after_tts_raises(flag_on):
    seg = {
        "segment_id": _uuid(2),
        "plain_text": "Hello world",
        "translation_uuid": "t" * 32,
    }
    bind(seg, text="Hello world", audio_path="a.wav", stage="post_tts")
    assert seg["identity_binding"]["tts_bound"] is True

    with pytest.raises(IdentityMismatchError) as exc:
        bind(
            seg,
            text="Completely different text",
            audio_path="b.wav",
            stage="post_tts_rebind",
            allow_rebind=False,
        )
    assert "rebind after TTS" in str(exc.value)

    # Intentional regen allowed
    bind(
        seg,
        text="Shortened hello",
        audio_path="c.wav",
        stage="regen",
        allow_rebind=True,
    )
    assert seg["identity_binding"]["audio_path"] == "c.wav"


def test_psa2_ba6ec_mismatch_path_caught(flag_on):
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    segs = []
    for row in data["segments"]:
        segs.append(
            {
                "segment_id": row["segment_id"],
                "plain_text": row["translated_text"],
                "translated_text": row["translated_text"],
                "final_tts_text": row["final_tts_text"],
                "owned_text_segment_id": row["segment_id"],
            }
        )

    with pytest.raises(IdentityMismatchError) as exc:
        assert_consistent(segs, stage="ba6ec_fixture")
    msg = str(exc.value).lower()
    assert "identity shift" in msg or "foreign" in msg


def test_psa2_ba6ec_flag_off_not_caught(flag_off):
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    segs = [
        {
            "segment_id": row["segment_id"],
            "plain_text": row["translated_text"],
            "final_tts_text": row["final_tts_text"],
        }
        for row in data["segments"]
    ]
    report = run_identity_guard(segs, stage="ba6ec_legacy")
    assert report["enabled"] is False
    assert report["ok"] is True


def test_psa2_bind_after_tts_detects_neighbor_text(flag_on):
    a = {
        "segment_id": _uuid(10),
        "plain_text": "Text for segment A",
    }
    b = {
        "segment_id": _uuid(11),
        "plain_text": "Text for segment B neighbor",
    }
    with pytest.raises(IdentityMismatchError):
        bind_after_tts(
            a,
            tts_text="Text for segment B neighbor",
            audio_path="a.wav",
            stage="post_tts",
            segments_data=[a, b],
        )


def test_psa2_remap_requires_uuid_map(flag_on):
    segs = [{"segment_id": _uuid(3), "plain_text": "x"}]
    with pytest.raises(IdentityMismatchError) as exc:
        remap_by_uuid(segs, {}, stage="resegment")
    assert "uuid_map" in str(exc.value).lower() or "index-only" in str(exc.value).lower()

    with pytest.raises(IdentityMismatchError):
        remap_by_uuid(
            segs,
            {"0": "1"},  # index-like
            stage="resegment",
        )


def test_psa2_remap_by_uuid_applies_payload(flag_on):
    old = _uuid(20)
    new = _uuid(21)
    segs = [
        {
            "segment_id": new,
            "plain_text": "fresh",
            "owned_text_segment_id": new,
        }
    ]
    out = remap_by_uuid(
        segs,
        {old: new},
        {old: {"plain_text": "remapped via uuid", "slot_ms": 1200}},
        stage="resegment",
    )
    assert out[0]["plain_text"] == "remapped via uuid"
    assert out[0]["slot_ms"] == 1200
    assert out[0]["segment_id"] == new


def test_psa2_foreign_wav_two_owners_raises(flag_on):
    wav = "shared.wav"
    segs = [
        {
            "segment_id": _uuid(30),
            "plain_text": "one",
            "final_tts_text": "one",
            "tts_file_path": wav,
        },
        {
            "segment_id": _uuid(31),
            "plain_text": "two",
            "final_tts_text": "two",
            "tts_file_path": wav,
        },
    ]
    with pytest.raises(IdentityMismatchError) as exc:
        assert_consistent(segs, stage="post_tts", require_wav=True)
    assert "wav" in str(exc.value).lower()


def test_psa2_text_content_hash_stable():
    assert text_content_hash("Hello  World") == text_content_hash("hello world")
    assert text_content_hash("a") != text_content_hash("b")
