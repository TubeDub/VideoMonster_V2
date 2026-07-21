"""Regression: MT meaning collapse on long George Lucas style segments."""

from __future__ import annotations

from engines.ai_core.translation_agent.confidence import translation_confidence
from engines.mt.sentence_split import is_severe_mt_collapse, split_mt_sentences


GEORGE_LONG = (
    "But, as he was driving, George Jr. could not help but feel like he was really "
    "dreading actually getting there. So George Jr. was a very smart kid, but he also "
    "got distracted really easily and because of that, he really had not pursued "
    "anything all that seriously that is except for cars. And at that point his father "
    "actually bought him a small Italian car called the Fiat, but his father, despite "
    "being the one who literally gave him the Fiat, he just didn't get his son's "
    "obsession with cars, like why aren't you able to take that focus and apply it to "
    "other things, so we'll get your real job. And so basically every dinner these "
    "days, if he came this huge argument between father and son. And so George, he came "
    "to this intersection where it was right near his home, and he begins making the "
    "turn when he hears this really loud screeching sound and then everything went black."
)

GEORGE_COLLAPSED = (
    "Джордж-молодший був дуже розумною дитиною, але він також відволікся дуже легко, "
    "і через це він не займався чимось серйозним, окрім машин."
)


def test_severe_mt_collapse_detects_163_to_21():
    assert len(GEORGE_LONG.split()) >= 100
    assert is_severe_mt_collapse(GEORGE_LONG, GEORGE_COLLAPSED)


def test_confidence_near_zero_on_collapse():
    conf = translation_confidence(
        translator_name="argos",
        success=True,
        attempt=1,
        source=GEORGE_LONG,
        translated=GEORGE_COLLAPSED,
    )
    assert conf < 0.2


def test_jr_does_not_split_sentences():
    parts = split_mt_sentences(
        "George Jr. drove home. Then everything went black."
    )
    assert len(parts) == 2
    assert parts[0].startswith("George Jr.")
    assert "black" in parts[1].lower()


def test_full_paragraph_stays_one_or_many_complete_sentences():
    parts = split_mt_sentences(GEORGE_LONG)
    joined = " ".join(parts)
    assert "Fiat" in joined
    assert "George Jr." in joined
    # Must not produce a lone "Jr" fragment
    assert not any(p.strip() in ("Jr.", "Jr") for p in parts)
