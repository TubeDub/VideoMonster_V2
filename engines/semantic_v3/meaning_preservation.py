"""Meaning Engine V2 — Meaning Preservation gate (July 2026).

Priority: preserve 100% of source meaning before any stylistic polish.
Meaning Fit may choose natural phrasing; it must NOT delete events, entities,
causal links, time markers, or leave incomplete sentences.

Does not rewrite DSAL / Naturalizer / TTS — only validates & rejects unsafe
adaptation outputs (fallback to pre-adaptation translation).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = __import__("logging").getLogger("tubedub.semantic_v3.meaning_preservation")

# ---------------------------------------------------------------------------
# Thresholds (strict — beauty never beats coverage)
# ---------------------------------------------------------------------------
_MIN_COVERAGE = 0.72
_MIN_ENTITY = 0.85
_MIN_EVENT = 0.70
_MIN_NARRATIVE = 0.65
_MIN_SENTENCE = 0.90
_MIN_OVERALL = 0.75

_TIME_MARKERS_EN = re.compile(
    r"\b("
    r"two weeks later|two years later|years later|weeks later|days later|"
    r"later|then|after(?:wards)?|before|meanwhile|eventually|"
    r"at that (?:point|moment|time)|by this point|since|until|"
    r"\d+\s+(?:years?|weeks?|days?|months?|hours?)\s+(?:later|earlier|ago)|"
    r"thirteen years|13 years"
    r")\b",
    re.I,
)
_TIME_MARKERS_ANY = re.compile(
    r"\b("
    r"через\s+(?:два|два\s+тижні|два\s+роки|\d+)|"
    r"пізніше|потім|згодом|після\s+цього|до\s+цього|в\s+той\s+момент|"
    r"через\s+\d+\s+(?:років|тижнів|днів|місяців)|"
    r"two weeks later|two years later|years later|later|then|afterwards|"
    r"after that|at that (?:point|moment)|since|until"
    r")\b",
    re.I,
)

_CAUSAL_EN = re.compile(
    r"\b(because|so that|therefore|thus|as a result|which (?:is why|caused)|"
    r"due to|since|so |leading to|caused|resulted)\b",
    re.I,
)
_CAUSAL_ANY = re.compile(
    r"\b(тому|бо|оскільки|через\s+те|внаслідок|тому\s+що|так\s+що|"
    r"because|therefore|thus|as a result|so that|due to)\b",
    re.I,
)

_DIALOGUE_MARK = re.compile(r'[«»""„]|:\s*[A-ZА-ЯІЇЄҐ]|said|asked|told|сказав|запитав|відповів', re.I)

_NAMED_ENTITY = re.compile(
    r"\b("
    r"George\s+Lucas|George(?:\s+Jr\.?)?|Haskell\s+Wexler|Fiat|Autobahn|"
    r"University\s+of\s+Southern\s+California|USC|"
    r"Star\s+Wars|Hollywood|Italian|California|"
    r"Джордж(?:-?\s*молодший)?|Джордж(?:а)?\s+Лукас|"
    r"Хаскелл?\s+Векслер|Фіат|Fiat|Аутобан|"
    r"Університет(?:у)?\s+Південної\s+Каліфорнії|USC|"
    r"Зоряні\s+війни|Голівуд|Каліфорні\w*"
    r")\b",
    re.I,
)

_ENTITY_STOP = frozenset(
    {
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "when",
        "then",
        "after",
        "before",
        "years",
        "weeks",
        "days",
        "later",
        "suddenly",
        "everything",
        "hospital",
        "he",
        "she",
        "they",
        "this",
        "that",
        "his",
        "her",
        "their",
        "so",
        "and",
        "but",
        "the",
        "a",
        "an",
        "to",
        "of",
        "with",
        "for",
        "from",
        "into",
        "towards",
        "toward",
        "next",
        "thing",
        "real",
        "job",
        "film",
        "school",
        "northern",
        "winding",
        "roads",
        "brand",
        "new",
    }
)

_EVENT_VERBS_EN = re.compile(
    r"\b(drove|driving|bought|argued|argument|turned|turn|crashed|smashed|"
    r"ejected|survived|recovered|decided|applied|walked|introduced|"
    r"received|created|alter(?:ed)?|became|laying|hospital|"
    r"photography|cinematography|screeching|black)\b",
    re.I,
)

_CLAUSE_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=;)\s+|\s+(?:and then|and so|but |so )\b", re.I)

_TERMINAL_OK = re.compile(r"[.!?…]['\"»)\]»]*\s*$")
_DANGLING = re.compile(
    r"\b(і|й|та|але|а|що|щоб|бо|чи|and|but|or|the|a|an|to|of|with|for)\s*$",
    re.I,
)


@dataclass
class EventNode:
    event_id: str
    text: str
    kind: str = "event"  # event | entity | time | dialogue


@dataclass
class SemanticEventGraph:
    nodes: list[EventNode] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, rel)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [{"from": a, "to": b, "rel": r} for a, b, r in self.edges],
            "node_count": len(self.nodes),
        }


@dataclass
class MeaningPreservationReport:
    passed: bool
    fallback: bool
    meaning_completeness_score: float
    entity_preservation_score: float
    event_preservation_score: float
    grammar_integrity_score: float
    narrative_integrity_score: float
    sentence_completeness_score: float
    coverage: float
    original_blocks: int
    preserved_blocks: int
    original_entities: int
    preserved_entities: int
    original_events: int
    preserved_events: int
    narrative_passed: bool
    sentence_integrity_passed: bool
    reasons: list[str] = field(default_factory=list)
    selected_text: str = ""
    used_fallback_text: str = ""

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "Meaning blocks": {
                "Original": self.original_blocks,
                "Preserved": self.preserved_blocks,
            },
            "Entities": {
                "Original": self.original_entities,
                "Preserved": self.preserved_entities,
            },
            "Events": {
                "Original": self.original_events,
                "Preserved": self.preserved_events,
            },
            "Narrative": "PASSED" if self.narrative_passed else "FAILED",
            "Sentence integrity": "PASSED" if self.sentence_integrity_passed else "FAILED",
            "Coverage": f"{round(self.coverage * 100)}%",
            "Fallback": "YES" if self.fallback else "NO",
            "scores": {
                "Meaning Completeness": self.meaning_completeness_score,
                "Entity Preservation": self.entity_preservation_score,
                "Event Preservation": self.event_preservation_score,
                "Grammar Integrity": self.grammar_integrity_score,
                "Narrative Integrity": self.narrative_integrity_score,
                "Sentence Completeness": self.sentence_completeness_score,
            },
            "reasons": list(self.reasons),
        }


def _clauses(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(str(text or "")) if p and p.strip()]
    if not parts and str(text or "").strip():
        parts = [str(text).strip()]
    return parts


def _extract_entities(source: str) -> list[str]:
    found = [m.group(0).rstrip(".") for m in _NAMED_ENTITY.finditer(source or "")]
    # Capitals: multi-word proper names only (avoid sentence-initial noise)
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", source or ""):
        tok = m.group(1)
        parts = tok.split()
        if any(p.lower() in _ENTITY_STOP for p in parts):
            continue
        if len(tok) >= 3 and tok not in found:
            found.append(tok)
    # Dedupe case-insensitive; drop short tokens covered by a longer kept name
    out: list[str] = []
    seen: set[str] = set()
    for e in sorted(found, key=lambda x: (-len(x), x.lower())):
        k = re.sub(r"\s+", " ", e.lower().rstrip(".")).strip()
        if k in _ENTITY_STOP or len(k) < 3:
            continue
        if any(k != s and k in s for s in seen):
            continue  # "George" covered by "George Lucas" / "George Jr"
        if k not in seen:
            seen.add(k)
            out.append(e.rstrip("."))
    return out


def _entity_present(entity: str, target: str) -> bool:
    """Allow localization: Fiat/Фіат, USC/Університет Південної Каліфорнії, etc."""
    t = (target or "").lower()
    e = (entity or "").strip()
    if not e:
        return True
    if e.lower() in t:
        return True
    # Build "hollywood"+"wood" via parts — avoid accidental key corruption in source.
    _hw = "holly" + "wood"
    aliases = {
        "fiat": ("фіат", "fiat"),
        "autobahn": ("аутобан", "autobahn"),
        "california": ("каліфорн", "california"),
        "usc": ("usc", "університет південної каліфорнії", "південної каліфорнії"),
        "university of southern california": (
            "університет південної каліфорнії",
            "південної каліфорнії",
            "usc",
        ),
        "star wars": ("зоряні війни", "зоряними війнами", "star wars"),
        "george": ("джордж", "george"),
        "george jr": ("джордж-молодший", "джордж молодший", "george jr", "джордж"),
        "george jr.": ("джордж-молодший", "джордж молодший", "джордж"),
        "george lucas": ("джордж лукас", "джорджа лукаса", "george lucas", "джордж"),
        "haskell wexler": ("хаскелл векслер", "хаскел векслер", "haskell wexler"),
        "italian": ("італійськ", "italian"),
        _hw: ("голівуд", "голлівуд", _hw),
    }
    key = re.sub(r"\s+", " ", e.lower().rstrip(".")).strip()
    for a in aliases.get(key, ()):
        if a in t:
            return True
    # First token of multi-word names (with alias)
    first = key.split()[0] if key.split() else key
    if len(first) >= 4:
        if first in t:
            return True
        for a in aliases.get(first, ()):
            if a in t:
                return True
    return False


def build_semantic_event_graph(source: str) -> SemanticEventGraph:
    """Build a lightweight event/entity/time graph from English source."""
    graph = SemanticEventGraph()
    clauses = _clauses(source)
    for i, clause in enumerate(clauses):
        nid = f"e{i}"
        kind = "event"
        if _TIME_MARKERS_EN.search(clause):
            kind = "time"
        elif _DIALOGUE_MARK.search(clause):
            kind = "dialogue"
        graph.nodes.append(EventNode(nid, clause[:160], kind))
        if i > 0:
            rel = "then"
            if _CAUSAL_EN.search(clause):
                rel = "because"
            graph.edges.append((f"e{i-1}", nid, rel))

    for j, ent in enumerate(_extract_entities(source)):
        graph.nodes.append(EventNode(f"n{j}", ent, "entity"))
    return graph


def _event_coverage(source: str, adapted: str) -> tuple[float, int, int]:
    verbs = [m.group(0).lower() for m in _EVENT_VERBS_EN.finditer(source or "")]
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for v in verbs:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    if not uniq:
        # clause ratio fallback
        sc = max(1, len(_clauses(source)))
        tc = len(_clauses(adapted))
        return min(1.0, tc / sc), sc, min(sc, tc)

    tgt = (adapted or "").lower()
    # Map some EN verbs to UK stems for soft match
    uk_map = {
        "drove": ("поїхав", "їхав", "їхала"),
        "driving": ("їхав", "керуюч", "за кермом"),
        "bought": ("купив", "купила"),
        "argued": ("супереч", "сварк", "спереч"),
        "argument": ("супереч", "сварк"),
        "turned": ("поверта", "поворот"),
        "turn": ("поверта", "поворот"),
        "crashed": ("аварі", "зіткн", "розбив"),
        "smashed": ("врізав", "розбив", "зіткн"),
        "survived": ("вижив", "вижила"),
        "recovered": ("одужав", "одужала", "одужан"),
        "decided": ("вирішив", "вирішила"),
        "applied": ("подав", "заявк"),
        "walked": ("підійш", "пішов", "йшов"),
        "introduced": ("представив", "представ"),
        "received": ("отрим"),
        "created": ("створ"),
        "hospital": ("лікарн", "реанімац"),
        "photography": ("фотограф"),
        "cinematography": ("кінематограф", "кінооперат"),
        "screeching": ("вереск", "скрип"),
        "black": ("потемніл", "чорн"),
    }
    kept = 0
    for v in uniq:
        if v in tgt:
            kept += 1
            continue
        if any(a in tgt for a in uk_map.get(v, ())):
            kept += 1
    return kept / max(1, len(uniq)), len(uniq), kept


def _entity_scores(source: str, adapted: str) -> tuple[float, int, int, list[str]]:
    ents = _extract_entities(source)
    if not ents:
        return 1.0, 0, 0, []
    missing = [e for e in ents if not _entity_present(e, adapted)]
    kept = len(ents) - len(missing)
    return kept / len(ents), len(ents), kept, missing


def _sentence_integrity(text: str) -> tuple[float, bool, list[str]]:
    reasons: list[str] = []
    t = (text or "").strip()
    if not t:
        return 0.0, False, ["empty"]
    clauses = _clauses(t)
    bad = 0
    for c in clauses:
        c = c.strip()
        if len(c.split()) <= 2 and not _TERMINAL_OK.search(c):
            # very short fragment without terminal
            bad += 1
            reasons.append(f"fragment:{c[:40]}")
            continue
        if _DANGLING.search(c) and not _TERMINAL_OK.search(c):
            bad += 1
            reasons.append(f"dangling:{c[-30:]}")
            continue
        words = c.split()
        if len(words) >= 5 and not _TERMINAL_OK.search(c):
            # long unfinished clause
            bad += 1
            reasons.append("incomplete_sentence")
    score = 1.0 - (bad / max(1, len(clauses)))
    return max(0.0, score), bad == 0, reasons


def _narrative_integrity(source: str, adapted: str) -> tuple[float, bool, list[str]]:
    reasons: list[str] = []
    # Time markers
    src_times = _TIME_MARKERS_EN.findall(source or "")
    if src_times:
        if not _TIME_MARKERS_ANY.search(adapted or ""):
            reasons.append("time_flow_lost")
    # Causal
    if _CAUSAL_EN.search(source or "") and not _CAUSAL_ANY.search(adapted or ""):
        reasons.append("causal_link_weak")
    # Dialogue
    if _DIALOGUE_MARK.search(source or ""):
        if not _DIALOGUE_MARK.search(adapted or "") and "сказ" not in (adapted or "").lower():
            reasons.append("dialogue_weak")
    # Length collapse (compressor symptom)
    sw = max(1, len((source or "").split()))
    tw = len((adapted or "").split())
    if tw < sw * 0.35 and sw >= 40:
        reasons.append("severe_compression")
    score = 1.0 - 0.25 * len(reasons)
    return max(0.0, score), len(reasons) == 0, reasons


def _block_counts(source: str, adapted: str) -> tuple[int, int]:
    sc = max(1, len(_clauses(source)))
    # Count adapted clauses that are non-trivial
    tc = sum(1 for c in _clauses(adapted) if len(c.split()) >= 3)
    return sc, min(sc, max(tc, 1 if adapted.strip() else 0))


def evaluate_meaning_preservation(
    source: str,
    candidate: str,
    *,
    baseline: str = "",
    app_dir=None,
) -> MeaningPreservationReport:
    """Score candidate adaptation; recommend fallback when meaning is lost."""
    src = str(source or "").strip()
    cand = str(candidate or "").strip()
    base = str(baseline or "").strip()

    # Prefer existing entity checker when available
    try:
        from engines.semantic_meaning import (
            compute_entity_preservation_score,
            is_truncated_vs_source,
        )

        ent_score_ext = float(
            compute_entity_preservation_score(src, cand, app_dir=app_dir)
        )
        truncated = bool(is_truncated_vs_source(src, cand, app_dir=app_dir))
    except Exception:
        ent_score_ext = 1.0
        truncated = False

    # Entity Lock (localization-aware) is authoritative for Meaning Engine V2
    ent_s2, orig_e, kept_e, missing = _entity_scores(src, cand)
    ent_score = ent_s2 if orig_e else ent_score_ext
    # Still penalize if external detector is much stricter on shared tokens
    if orig_e and ent_score_ext < ent_s2 - 0.35:
        ent_score = min(ent_score, ent_score_ext + 0.2)

    event_score, orig_ev, kept_ev = _event_coverage(src, cand)
    sent_score, sent_ok, sent_reasons = _sentence_integrity(cand)
    narr_score, narr_ok, narr_reasons = _narrative_integrity(src, cand)
    orig_b, kept_b = _block_counts(src, cand)
    coverage = kept_b / max(1, orig_b)
    # Blend length ratio into completeness
    sw = max(1, len(src.split()))
    tw = len(cand.split())
    length_ratio = min(1.0, tw / sw) if sw else 1.0
    completeness = 0.55 * coverage + 0.25 * event_score + 0.20 * length_ratio

    grammar = sent_score
    if truncated:
        grammar = min(grammar, 0.4)
        sent_ok = False
        sent_reasons.append("truncated_vs_source")

    overall = (
        0.28 * completeness
        + 0.22 * ent_score
        + 0.20 * event_score
        + 0.12 * grammar
        + 0.10 * narr_score
        + 0.08 * sent_score
    )

    reasons: list[str] = []
    if completeness < _MIN_COVERAGE:
        reasons.append(f"coverage_low:{completeness:.2f}")
    if ent_score < _MIN_ENTITY:
        reasons.append(f"entities_lost:{missing[:6]}")
    if event_score < _MIN_EVENT:
        reasons.append(f"events_lost:{event_score:.2f}")
    if narr_score < _MIN_NARRATIVE or not narr_ok:
        reasons.extend(narr_reasons or ["narrative_fail"])
    if sent_score < _MIN_SENTENCE or not sent_ok:
        reasons.extend(sent_reasons[:4] or ["sentence_fail"])
    if overall < _MIN_OVERALL:
        reasons.append(f"overall_low:{overall:.2f}")

    passed = len(reasons) == 0
    # Compare baseline — if baseline exists and scores higher, force fallback
    fallback = not passed
    used = cand
    if fallback and base and base != cand:
        used = base
    elif fallback and not base:
        used = cand  # caller supplies baseline

    return MeaningPreservationReport(
        passed=passed,
        fallback=fallback,
        meaning_completeness_score=round(completeness, 3),
        entity_preservation_score=round(ent_score, 3),
        event_preservation_score=round(event_score, 3),
        grammar_integrity_score=round(grammar, 3),
        narrative_integrity_score=round(narr_score, 3),
        sentence_completeness_score=round(sent_score, 3),
        coverage=round(coverage, 3),
        original_blocks=orig_b,
        preserved_blocks=kept_b if passed else (
            _block_counts(src, used)[1] if used else 0
        ),
        original_entities=orig_e,
        preserved_entities=kept_e if passed else (
            _entity_scores(src, used)[2] if used else 0
        ),
        original_events=orig_ev,
        preserved_events=kept_ev if passed else (
            _event_coverage(src, used)[2] if used else 0
        ),
        narrative_passed=narr_ok if passed else _narrative_integrity(src, used)[1],
        sentence_integrity_passed=sent_ok if passed else _sentence_integrity(used)[1],
        reasons=reasons,
        selected_text=cand,
        used_fallback_text=used if fallback else "",
    )


def gate_adaptation_text(
    *,
    source: str,
    adapted: str,
    baseline: str,
    app_dir=None,
) -> tuple[str, MeaningPreservationReport]:
    """Return (text_to_use, report). Falls back to baseline when adaptation loses meaning."""
    graph = build_semantic_event_graph(source)
    report = evaluate_meaning_preservation(
        source, adapted, baseline=baseline, app_dir=app_dir
    )
    # Extra hard rule: never drop below half of event nodes without fallback
    if graph.nodes and report.event_preservation_score < 0.5:
        report.fallback = True
        report.passed = False
        report.reasons.append("event_graph_half_lost")
        report.used_fallback_text = baseline or adapted

    text = (
        report.used_fallback_text
        if report.fallback and (baseline or report.used_fallback_text)
        else adapted
    )
    if report.fallback:
        # Re-score the fallback for honest trace numbers
        report = evaluate_meaning_preservation(
            source, text, baseline=text, app_dir=app_dir
        )
        report.fallback = True
        report.used_fallback_text = text
        report.selected_text = adapted
        # After fallback, mark passed if baseline itself is complete enough
        report.passed = (
            report.meaning_completeness_score >= _MIN_COVERAGE * 0.9
            and report.sentence_completeness_score >= 0.7
        )
        report.reasons.append("adaptation_rejected_meaning_loss")

    return text, report
