"""TRH must never truncate Final/TTS via [:500] audit previews."""

from __future__ import annotations


def test_stamp_keeps_full_approved_and_naturalized():
    from engines.trh import stamp_segment_recovery

    long_nat = (
        "Тепер Джордж-молодший пішов до подіуму, щоб взяти деякі фотографії переможця. "
        "Але, як він прогулявся там, чоловік середнього віку прийшов назустріч йому і "
        "просто попросив Джорджа-молодшого про свою фотографію, а потім в деякій точці "
        "людина фактично офіційно представився як Хаскелл Векслер. І сказав він, що він "
        "насправді був кінооператором в Голлівуді. Про те, як він нещодавно звернувся до "
        "Університету Південної Каліфорнії, щоб спробувати потрапити в програму "
        "кінематографії. А коли Хаскелл почув це, він сказав, Джордж, я знаю людей з "
        "Університету Південної Каліфорнії."
    )
    assert len(long_nat) > 500
    seg: dict = {}
    stamp_segment_recovery(
        seg,
        original="Now George Jr. walked...",
        raw_mt="raw",
        naturalized=long_nat,
        approved="",  # manual path
        tps_path="manual",
        tqe_status="FAIL_MANUAL_REVIEW",
    )
    assert seg["trh"]["naturalized"] == long_nat
    assert len(seg["trh"]["naturalized"]) > 500
    # Empty approved must NOT fall back to truncated naturalized
    assert seg["trh"]["approved"] == ""
    assert len(seg["trh"]["approved_preview"]) <= 500


def test_sync_audits_prefers_full_final_over_truncated_trh():
    from engines.trh import sync_audits_trh

    full = ("X" * 600) + " кінець."
    truncated = full[:500]
    info = {
        "segments_data": [
            {
                "index": 0,
                "raw_mt": "raw",
                "naturalized_text": full,
                "approved_text": "",  # manual
                "final_text": full,
                "tps_path": "manual",
                "tqe_status": "FAIL_MANUAL_REVIEW",
                "trh": {
                    "raw_mt": "raw",
                    "naturalized": truncated,  # old buggy snapshot
                    "approved": truncated,
                    "dirty": True,
                    "changed_text": True,
                    "tps_path": "manual",
                },
            }
        ],
        "translation_audits": [{"index": 0}],
    }
    sync_audits_trh(info)
    audit = info["translation_audits"][0]
    assert audit["final_text"] == full
    assert audit["tts_text"] == full
    assert len(audit["final_text"]) > 500
    assert not audit["final_text"].endswith(truncated[-10:]) or audit["final_text"].endswith("кінець.")


def test_heal_truncated_final_from_naturalized():
    from engines.trh import heal_truncated_final, sync_audits_trh
    from engines.translation_review import _resolve_final_text

    full = (
        "Тепер Джордж-молодший пішов до подіуму, щоб взяти деякі фотографії переможця. "
        "Але, як він прогулявся там, чоловік середнього віку прийшов назустріч йому і "
        "просто попросив Джорджа-молодшого про свою фотографію, а потім в деякій точці "
        "людина фактично офіційно представився як Хаскелл Векслер. І сказав він, що він "
        "насправді був кінооператором в Голлівуді. Про те, як він нещодавно звернувся до "
        "Університету Південної Каліфорнії, щоб спробувати потрапити в програму "
        "кінематографії. А коли Хаскелл почув це, він сказав, Джордж, я знаю людей з "
        "Університету Південної Каліфорнії."
    )
    cut = full[:500]
    assert heal_truncated_final(cut, full) == full

    # Old task shape: truncated Final, full Naturalized
    info = {
        "segments_data": [
            {
                "index": 0,
                "raw_mt": "raw",
                "naturalized_text": full,
                "approved_text": "",
                "final_text": cut,
                "tps_path": "manual",
                "trh": {"approved": cut, "naturalized": full, "tps_path": "manual"},
            }
        ],
        "translation_audits": [
            {"index": 0, "naturalized_text": full, "final_text": cut, "approved_text": cut}
        ],
    }
    sync_audits_trh(info)
    assert info["translation_audits"][0]["final_text"] == full
    assert info["segments_data"][0]["final_text"] == full

    healed = _resolve_final_text(
        {"approved_text": cut, "naturalized_text": full},
        {"approved_text": cut, "naturalized_text": full},
    )
    assert healed == full
    assert "Хаскелл почув це" in healed


def test_fiat_accepted_as_uk_phiат():
    from engines.translation_quality import missing_preserved_tokens

    missing = missing_preserved_tokens(
        "his father bought him a Fiat",
        "батько купив йому Фіат",
    )
    assert "Fiat" not in missing
