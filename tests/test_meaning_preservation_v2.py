"""Meaning Engine V2 — Meaning Preservation regression tests.

George Lucas–style long narrative: events, entities, causal/time flow,
incomplete sentences, and fallback when adaptation compresses meaning away.
"""

from __future__ import annotations

from engines.semantic_v3.meaning_preservation import (
    build_semantic_event_graph,
    evaluate_meaning_preservation,
    gate_adaptation_text,
)

GEORGE_SRC = (
    "Two weeks later, George Jr. was driving his brand new, souped-up Autobahn "
    "agent through the winding roads of northern California when he looked down "
    "to tune the radio. Suddenly he looked up only to find himself hurtling "
    "towards the trees. He turned the wheel hard and the next thing he knew "
    "he was heading straight for a ditch. He heard the screeching of tires and "
    "everything went black. When he woke up in hospital, he had been thrown "
    "clear of the car. He had survived. "
    "After his recovery, George decided that he needed a real job. He applied "
    "to the University of Southern California film school. USC accepted him. "
    "Haskell Wexler introduced him to Hollywood cinematography. Years later "
    "George Lucas created Star Wars."
)

GEORGE_FULL_UK = (
    "Через два тижні Джордж-молодший їхав своїм новим потужним Autobahn "
    "звивистими дорогами північної Каліфорнії, коли він глянув униз, щоб "
    "налаштувати радіо. Раптом він підняв очі й побачив, що мчить просто "
    "на дерева. Він різко повернув кермо, і наступної миті вже летів у рів. "
    "Він почув вереск шин, і все потемніло. Коли він прокинувся в лікарні, "
    "виявилося, що його викинуло з машини. Він вижив. "
    "Після одужання Джордж вирішив, що йому потрібна справжня робота. "
    "Він подав заявку до Університету Південної Каліфорнії на кінофакультет. "
    "USC його прийняв. Хаскелл Векслер познайомив його з голлівудською "
    "кінематографією. Роками пізніше Джордж Лукас створив Зоряні війни."
)

GEORGE_COMPRESSED_BAD = "Джордж був дуже розумною дитиною. справжню роботу досвід на межі смерті"

GEORGE_FRAGMENT = "досвід на межі смерті"


def test_event_graph_keeps_key_nodes():
    graph = build_semantic_event_graph(GEORGE_SRC)
    assert len(graph.nodes) >= 5
    blobs = " ".join(n.text for n in graph.nodes).lower()
    assert "hospital" in blobs or "survived" in blobs or "driving" in blobs
    assert any(n.kind == "entity" for n in graph.nodes)


def test_full_uk_preserves_events_and_entities():
    report = evaluate_meaning_preservation(GEORGE_SRC, GEORGE_FULL_UK)
    assert report.event_preservation_score >= 0.70
    assert report.entity_preservation_score >= 0.85
    assert report.sentence_integrity_passed
    assert report.coverage >= 0.70
    assert not report.fallback


def test_compressed_adaptation_triggers_fallback():
    text, report = gate_adaptation_text(
        source=GEORGE_SRC,
        adapted=GEORGE_COMPRESSED_BAD,
        baseline=GEORGE_FULL_UK,
    )
    assert report.fallback is True
    assert text == GEORGE_FULL_UK
    assert "adaptation_rejected_meaning_loss" in report.reasons


def test_entity_lock_rejects_missing_usc_star_wars():
    stripped = (
        "Через два тижні хлопець їхав машиною і потрапив в аварію. "
        "Потім він пішов учитися і зняв якийсь фільм."
    )
    report = evaluate_meaning_preservation(GEORGE_SRC, stripped)
    assert report.entity_preservation_score < 0.85
    assert report.fallback or not report.passed


def test_causal_and_time_flow_protection():
    no_time = (
        "Джордж-молодший їхав машиною, врізався й опинився в лікарні. "
        "Він вижив. Він подав заявку до USC. Джордж Лукас створив Зоряні війни."
    )
    # Missing "two weeks later" / "years later" markers
    score, ok, reasons = __import__(
        "engines.semantic_v3.meaning_preservation", fromlist=["_narrative_integrity"]
    )._narrative_integrity(GEORGE_SRC, no_time)
    assert "time_flow_lost" in reasons or score < 1.0


def test_incomplete_sentence_rejected():
    text, report = gate_adaptation_text(
        source=GEORGE_SRC,
        adapted=GEORGE_FRAGMENT,
        baseline=GEORGE_FULL_UK,
    )
    assert report.fallback is True
    assert text == GEORGE_FULL_UK
    assert report.sentence_completeness_score < 1.0 or any(
        "fragment" in r or "incomplete" in r or "adaptation_rejected" in r
        for r in report.reasons
    )


def test_trace_dict_shape():
    report = evaluate_meaning_preservation(GEORGE_SRC, GEORGE_FULL_UK)
    trace = report.to_trace_dict()
    assert "Meaning blocks" in trace
    assert "Entities" in trace
    assert "Events" in trace
    assert "Coverage" in trace
    assert "Fallback" in trace
    assert trace["Fallback"] in {"YES", "NO"}


def test_fit_engine_fallback_on_lossy_unit():
    from engines.semantic_v3.types import SemanticSentence
    from engines.semantic_v3.meaning_fit_engine import fit_meaning_units_to_target

    # Unit with a deliberately short "translation" that looks like a compressor
    # output — gate must keep baseline if adaptation variants lose meaning.
    # We patch generate path by providing already-translated full text as baseline;
    # fit engine uses translated_text as baseline then may pick compact variants.
    sent = SemanticSentence(
        text=GEORGE_SRC,
        start_ms=0,
        end_ms=45000,
        words=[],
        translated_text=GEORGE_FULL_UK,
    )
    out = fit_meaning_units_to_target([sent], voice="default", tgt_lang="uk")
    assert out
    final = (out[0].translated_text or "").strip()
    assert len(final.split()) >= 40
    mp = getattr(out[0], "meaning_preservation", None)
    assert isinstance(mp, dict)
    assert "Coverage" in mp
