"""MF1 — Meaning Fit flags default OFF + skeleton no-op; legacy OK."""

from __future__ import annotations

import pytest

from engines.meaning_fit import (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_SHORTEN,
    FitRequest,
    FitResult,
    MeaningText,
    TruncateNotMeaningFitError,
    fit_meaning,
    list_mf1_flags,
    meaning_fit_before_lock_flag,
    meaning_fit_expand_flag,
    meaning_fit_flag,
    meaning_fit_shorten_flag,
    reject_truncate_as_success,
    skeleton_meaning_fit,
)

_MF1_ENVS = (
    VM_FLAG_MEANING_FIT,
    VM_FLAG_MEANING_FIT_SHORTEN,
    VM_FLAG_MEANING_FIT_EXPAND,
    VM_FLAG_MEANING_FIT_BEFORE_LOCK,
)


@pytest.fixture
def clear_mf1_env(monkeypatch):
    for key in _MF1_ENVS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_mf1_flags_default_off(clear_mf1_env, monkeypatch):
    """Explicit env=0 forces OFF (hotfix: config/dubbing may default ON)."""
    for key in _MF1_ENVS:
        monkeypatch.setenv(key, "0")
    flags = list_mf1_flags()
    assert flags["meaning_fit"] is False
    assert flags["meaning_fit_shorten"] is False
    assert flags["meaning_fit_expand"] is False
    assert flags["meaning_fit_before_lock"] is False
    assert meaning_fit_flag() is False
    assert meaning_fit_shorten_flag() is False
    assert meaning_fit_expand_flag() is False
    assert meaning_fit_before_lock_flag() is False


def test_mf1_flag_env_on(monkeypatch, clear_mf1_env):
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    monkeypatch.setenv(VM_FLAG_MEANING_FIT_SHORTEN, "0")
    assert meaning_fit_flag() is True
    assert meaning_fit_shorten_flag() is False


def test_mf1_skeleton_noop_when_flag_off(clear_mf1_env, monkeypatch):
    for key in _MF1_ENVS:
        monkeypatch.setenv(key, "0")
    text = "Коза паслась на тій горі і їла траву"
    out = skeleton_meaning_fit(text, slot_ms=2500)
    assert out["meta"]["enabled"] is False
    assert out["meta"]["noop"] is True
    assert out["status"] == "noop"
    assert out["text_uk"] == text
    assert out["success"] is False

    res = fit_meaning(FitRequest(text_uk=text, slot_ms=2500))
    assert isinstance(res, FitResult)
    assert res.reason == "flag_off_legacy"
    assert res.text_uk == text


def test_mf1_skeleton_flag_on_still_stub(monkeypatch, clear_mf1_env):
    """Flag ON: MF1 stub era → now duration path may already_fits (not crash)."""
    monkeypatch.setenv(VM_FLAG_MEANING_FIT, "1")
    res = fit_meaning(FitRequest(text_uk="Коза паслась там на лугу", slot_ms=2500))
    assert res.status in ("noop", "already_fits", "paraphrase_shorten", "fit_failed")
    assert res.text_uk == "Коза паслась там на лугу"


def test_mf1_truncate_to_n_chars_forbidden_as_success(clear_mf1_env):
    with pytest.raises(TruncateNotMeaningFitError):
        reject_truncate_as_success("truncate_to_n_chars", text_uk="Коза паслась на тій")

    with pytest.raises(TruncateNotMeaningFitError):
        fit_meaning(
            FitRequest(
                text_uk="Коза паслась на тій",
                slot_ms=2500,
                meta={"method": "truncate_to_n_chars"},
            )
        )


def test_mf1_types_exist():
    mt = MeaningText(text="x", lang="uk")
    assert mt.stripped() == "x"
    req = FitRequest(text_uk="y", slot_ms=1000)
    assert req.slot_ms == 1000
    res = FitResult(text_uk="y", status="noop")
    assert res.as_dict()["status"] == "noop"


def test_mf1_flag_off_legacy_passthrough_ok(clear_mf1_env, monkeypatch):
    """flag=0 → legacy OK: input text returned unchanged, no raise."""
    for key in _MF1_ENVS:
        monkeypatch.setenv(key, "0")
    original = "Коза паслась на тій горі і їла траву"
    res = fit_meaning({"text_uk": original, "slot_ms": 2500})
    assert res.text_uk == original
    assert res.status == "noop"
    assert res.meta.get("noop") is True
