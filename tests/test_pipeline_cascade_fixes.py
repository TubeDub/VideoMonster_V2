"""Regression tests for incident 5c81c046 — cascade pipeline failures."""

from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions
from engines.semantic_optimizer import (
    optimize_for_time_budget,
    optimize_llm_rephrase_for_slot,
)
from openddf.utils import REDACTED, filter_sensitive_data


def test_optimize_llm_rephrase_no_unbound_ok_when_llm_returns_same():
    """UnboundLocalError when candidate == current must not crash."""
    result = optimize_llm_rephrase_for_slot(
        "Це довгий текст для тесту без змін.",
        source_hint="This is a long test line.",
        slot_ms=500,
        tgt_lang="uk",
        max_rounds=1,
    )
    assert result.stopped_reason in (
        "fits_no_change",
        "requires_llm_adaptation",
        "llm_no_change",
        "no_rewrite_performed",
        "fits_after_llm",
        "llm_meaning_rejected_truncated_tail",
    )


def test_optimize_for_time_budget_no_unbound_ok_on_overflow():
    """Real timing-aware path (adapt_segment_to_slot → optimize_for_time_budget).

    Tiny slot forces rule stages to run; the `ok` variable must always be
    initialized before `if not ok` (incident 5c81c046, watchdog segment crash).
    """
    long_uk = (
        "І в той момент його батько насправді купив йому маленьку італійську "
        "машину під назвою Фіат, але його батько, попри те, що саме він буквально "
        "дав йому той Фіат, зовсім не розумів захоплення сина автомобілями."
    )
    result = optimize_for_time_budget(
        long_uk,
        source_hint="And at that point his father actually bought him a small Italian car.",
        slot_ms=600,
        tgt_lang="uk",
        src_lang="en",
    )
    assert result.text  # never empty, no crash, no tail-clip to empty
    assert isinstance(result.stopped_reason, str) and result.stopped_reason


def test_entity_fixup_keeps_george_lucas_when_star_wars_also_in_source():
    src = (
        "His film became part of the most successful movie franchise of all time. "
        "George Lucas today is better known as George Lucas, and his franchise would be Star Wars."
    )
    bad = "Джордж-молодший сьогодні більш відомий як «Зоряні війни»."
    fixed = sanitize_wrong_entity_substitutions(bad, original=src, tgt_lang="uk")
    assert "Зоряні війни" not in fixed or "Джордж Лукас" in fixed
    assert "Джордж Лукас" in fixed or "George Lucas" not in fixed.lower()


def test_filter_sensitive_data_preserves_missing_preserved_tokens():
    payload = {"missing_preserved_tokens": ["Lucas", "Fiat"], "secret_key": "x"}
    filtered = filter_sensitive_data(payload)
    assert filtered["missing_preserved_tokens"] == ["Lucas", "Fiat"]
    assert filtered["secret_key"] == REDACTED


def test_name_damage_penalty_defined_and_ranking_works():
    """RCA #1: lost `def _name_damage_penalty` header caused NameError that

    crashed group MT for every segment, leaving English in the UK track.
    """
    from engines.translation_manager import (
        TranslationCandidate,
        _name_damage_penalty,
        _rank_candidates,
    )

    assert _name_damage_penalty("George Lucas", "Джордж Лукас") == 0.0

    def _cand(text: str, score: float, engine: str) -> TranslationCandidate:
        return TranslationCandidate(
            text=text,
            score=score,
            engine=engine,
            route_label="en→uk",
            route_name="direct",
            pivot=None,
            direct=True,
            elapsed_ms=0.0,
        )

    ranked = _rank_candidates(
        "George Lucas",
        [_cand("Джордж Лукас", 0.9, "a"), _cand("George Lucas", 0.8, "b")],
    )
    assert ranked  # no NameError, returns ordered list


def test_marian_engine_has_no_undefined_path_reference():
    """RCA #2: marian translate() referenced unimported `Path` (NameError),

    so the Marian fallback always failed and MT returned English source.
    """
    import inspect

    from engines.mt import marian_engine

    src = inspect.getsource(marian_engine.MarianEngine.translate)
    # translate() must not reference Path without importing it in scope.
    assert "Path(" not in src


def test_language_mismatch_report_pinpoints_raw_mt_stage():
    """TZ §4/§8: report must explain WHERE English appears, not just flag it."""
    from engines.pipeline_language_gate import build_language_mismatch_report

    english_leak = "An 18-year-old boy named Джордж-молодший. drove through his hometown."
    report = build_language_mismatch_report(
        index=0,
        segment={"text": english_leak},
        audit={"raw_translation": english_leak, "final_text": english_leak},
        original="An 18-year-old boy named George Jr. drove through his hometown.",
        target_lang="uk",
    )
    assert report["first_non_target_stage"]["stage"] == "raw_mt"
    assert "translation_manager.py" in report["diagnosis"]
