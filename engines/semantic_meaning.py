"""
Semantic meaning preservation for dubbing text adaptation.

Forbidden: tail clipping, ellipsis truncation, dropping em-dash / parenthetical blocks.
Allowed: rephrase, shorter synonyms, natural spoken constructions.
"""

from __future__ import annotations

import re
from typing import Any

from engines.translation_quality import (
    extract_preserved_tokens,
    missing_preserved_tokens,
    _source_entity_tokens,
)

_ELLIPSIS_END = re.compile(r"(?:…|\.\.\.)[\s\"'»»]*$")
_INCOMPLETE_END = re.compile(r"[,;:—–-]\s*$")

# Long UK/RU spoken forms → shorter natural alternatives (TZ §6).
# NEVER strip «не міг не » to "" — that left bare infinitives («відчути»).
_UK_COMPACT_PHRASES: list[tuple[str, str]] = [
    (r"\bне\s+міг\s+не\s+відчувати,?\s*що\b", "відчував, що"),
    (r"\bне\s+міг\s+не\s+відчути,?\s*що\b", "відчув, що"),
    (r"\bне\s+міг\s+не\s+помітити,?\s*що\b", "помітив, що"),
    (r"\bне\s+міг\s+не\s+сказати,?\s*що\b", "сказав, що"),
    (r"\bне\s+міг\s+допомогти\s+собі\s+", ""),
    (r"\bне\s+міг\s+позбутися\s+відчуття\b", "його не полишала тривога"),
]
_RU_COMPACT_PHRASES: list[tuple[str, str]] = [
    (r"\bне\s+мог\s+не\s+почувствовать,?\s*что\b", "почувствовал, что"),
    (r"\bне\s+мог\s+не\s+заметить,?\s*что\b", "заметил, что"),
    (r"\bне\s+мог\s+помочь\s+но\s+", ""),
    (r"\bне\s+мог\s+избавиться\s+от\s+ощущения\b", "его не покидало ощущение"),
    (r"\bне\s+мог\s+избавиться\s+от\s+чувства\b", "его не покидало чувство"),
]
_EN_COMPACT_PHRASES: list[tuple[str, str]] = [
    (r"\bcould\s+not\s+help\s+but\s+feel\b", "kept feeling"),
    (r"\breally\s+dreading\b", "dreading"),
    (r"\bactually\s+getting\s+there\b", "arriving"),
]

# Generic «не міг не + infinitive» → past-tense stem (never bare infinitive).
_UK_COULD_NOT_HELP = re.compile(
    r"\bне\s+міг\s+не\s+([а-яіїєґ]{3,}ти)\b",
    re.I,
)
_RU_COULD_NOT_HELP = re.compile(
    r"\bне\s+мог\s+не\s+([а-яё]{3,}ть)\b",
    re.I,
)

_ACTION_CUES_EN = re.compile(
    r"\b(driving|drive|dread|feel|feeling|getting|arrive|arriving|could\s+not|cannot|help\s+but)\b",
    re.I,
)


def word_count(text: str) -> int:
    return len(str(text or "").split())


def _norm_token(tok: str) -> str:
    """Lowercase, strip stress marks + punctuation for tail comparison."""
    import unicodedata

    s = unicodedata.normalize("NFD", str(tok or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^\w]", "", s, flags=re.UNICODE).lower()


def _ending_preserved(original: str, adapted: str, *, tail: int = 2) -> bool:
    """True when the adapted text keeps the original sentence's ending.

    A legitimate shortening (filler/synonym removal, rephrase) ends on the SAME
    final content word(s) as the original and closes with terminal punctuation.
    Real truncation drops the tail, so the endings diverge.

    Also accept paraphrases that close cleanly and keep the original's last
    content word somewhere in the final few tokens (e.g. лікарні→лікарні,
    гонщика→переможця with shared stem / closing punct).
    """
    adpt = str(adapted or "").strip()
    if not adpt or adpt[-1] not in ".!?…":
        return False
    o_tokens = [t for t in (_norm_token(w) for w in str(original or "").split()) if t]
    a_tokens = [t for t in (_norm_token(w) for w in adpt.split()) if t]
    if not o_tokens or not a_tokens:
        return False
    # Exact final-word match (classic filler drop).
    if o_tokens[-1] == a_tokens[-1]:
        return True
    # Paraphrase: original ending still present near the close.
    tail_a = set(a_tokens[-max(3, tail + 1) :])
    if o_tokens[-1] in tail_a:
        return True
    # Shared stem (≥4 chars) between last originals and last adapted words.
    for ot in o_tokens[-2:]:
        if len(ot) < 4:
            continue
        stem = ot[:4]
        if any(at.startswith(stem) or stem.startswith(at[:4]) for at in a_tokens[-3:]):
            return True
    return False


def is_truncated_adaptation(original: str, adapted: str) -> bool:
    """Detect tail clip / ellipsis truncation.

    Large word-count drops are NOT truncation when the sentence ending is fully
    preserved — that is a legitimate filler/synonym rephrase (TZ §1/§4: forbid
    mechanical clipping, allow intelligent shortening).
    """
    orig = str(original or "").strip()
    adpt = str(adapted or "").strip()
    if not adpt or adpt == orig:
        return False
    if _ELLIPSIS_END.search(adpt):
        return True
    ow, aw = word_count(orig), word_count(adpt)
    if ow >= 8 and aw < int(ow * 0.72) and _INCOMPLETE_END.search(adpt):
        return True
    # Missing terminal punct on a shortened line ≈ cut mid-thought.
    if (
        ow >= 6
        and aw < ow
        and adpt[-1] not in ".!?…"
        and orig[-1] in ".!?…"
    ):
        return True
    if ow >= 10 and aw <= int(ow * 0.65):
        # Pure length drop → truncation only if the ending was actually lost.
        if not _ending_preserved(orig, adpt):
            return True
    return False


def restore_terminal_close(text: str, *, original: str = "", reference: str = "") -> str:
    """Ensure spoken line closes when EN/reference sentence is complete."""
    out = str(text or "").strip()
    if not out:
        return out
    if out[-1] in ".!?…»\"')":
        return out
    src = str(original or "").strip()
    ref = str(reference or "").strip()
    src_complete = bool(src) and src[-1] in ".!?…"
    ref_complete = bool(ref) and ref[-1] in ".!?…"
    # Mid-clause Whisper cuts must stay without forced period.
    src_incomplete = bool(src) and src[-1] not in ".!?…" and word_count(src) >= 6
    if src_incomplete and not src_complete:
        return out
    if (src_complete or ref_complete) and word_count(out) >= 3:
        return out + "."
    return out


def _uk_infinitive_to_past(inf: str) -> str:
    """Best-effort UK infinitive → masculine past (spoken dubbing)."""
    w = str(inf or "").lower()
    if w.endswith("ити") and len(w) > 4:
        return w[:-3] + "ив"  # помітити → помітив
    if w.endswith("ати") and len(w) > 4:
        return w[:-3] + "ав"  # сказати → сказав
    if w.endswith("іти") and len(w) > 4:
        return w[:-3] + "ів"
    if w.endswith("ти") and len(w) > 3:
        return w[:-2] + "в"  # відчути → відчув
    return w


def apply_compact_phrases(text: str, *, target_lang: str | None = None) -> str:
    """Replace heavy calques with shorter natural spoken forms.

    Language-gated: UK rows never rewrite Russian (and vice versa).
    """
    out = str(text or "").strip()
    if not out:
        return out
    lang = (target_lang or "").split("-")[0].lower()
    if lang not in ("uk", "ru", "en"):
        return out
    table = (
        _UK_COMPACT_PHRASES
        if lang == "uk"
        else _RU_COMPACT_PHRASES
        if lang == "ru"
        else _EN_COMPACT_PHRASES
    )
    for pattern, repl in table:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    if lang == "uk":
        out = _UK_COULD_NOT_HELP.sub(
            lambda m: _uk_infinitive_to_past(m.group(1)), out
        )
    elif lang == "ru":
        out = _RU_COULD_NOT_HELP.sub(
            lambda m: (
                m.group(1)[:-2] + "л"
                if m.group(1).lower().endswith("ть")
                else m.group(1)
            ),
            out,
        )
    return " ".join(out.split())


def verify_meaning_preserved(
    source: str,
    original_translation: str,
    adapted: str,
    *,
    target_lang: str | None = None,
    app_dir=None,
    is_source_segment_incomplete: bool = False,  # New parameter
) -> tuple[bool, str, list[str]]:
    """
    Check actors, preserved tokens, no truncation, complete sentence.
    Returns (ok, reason_code, missing_hints).
    """
    src = str(source or "").strip()
    orig = str(original_translation or "").strip()
    adpt = str(adapted or "").strip()
    if not adpt:
        return False, "empty", []
    if adpt == orig:
        return True, "unchanged", []

    if is_truncated_adaptation(orig, adpt):
        return False, "truncated_tail", ["ellipsis_or_incomplete"]

    missing = missing_preserved_tokens(
        src,
        adpt,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    )  # Pass new parameter
    if missing:
        return False, "preserved_token", missing[:8]

    # Incomplete terminal punctuation: skip false positives when source is also
    # mid-clause (Whisper/ASR cut) or caller marked the source segment incomplete.
    src_incomplete = bool(is_source_segment_incomplete) or (
        bool(src)
        and src[-1] not in ".!?…"
        and word_count(src) >= 6
    )
    if (
        adpt
        and adpt[-1] not in ".!?…"
        and word_count(adpt) >= 6
        and not src_incomplete
    ):
        return False, "incomplete_sentence", []

    # Source action/emotion cues should not all disappear in long lines
    if word_count(src) >= 10 and _ACTION_CUES_EN.search(src):
        cues = _ACTION_CUES_EN.findall(src)
        if cues and not any(c.lower() in adpt.lower() for c in cues[:3]):
            # Soft check — only fail if many cues lost AND big word drop
            if word_count(adpt) < int(word_count(orig) * 0.7):
                return False, "lost_action_cues", cues[:5]

    actors = _source_entity_tokens(src, app_dir=app_dir)
    if actors:
        missing_actors = missing_preserved_tokens(
            src,
            adpt,
            app_dir=app_dir,
            is_source_segment_incomplete=is_source_segment_incomplete,
        )  # Pass new parameter
        for name in actors:
            if name in missing_actors:
                return False, "lost_actor", [name]

    return True, "ok", []


class SemanticValidationError(Exception):
    """Raised when post-semantic chain validation fails (TZ §6, §8)."""

    code = "SEMANTIC_VALIDATION"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}
        self.failures = list(self.details.get("failures") or [])
        first = self.failures[0] if self.failures else {}
        first_err = (first.get("errors") or [{}])[0] if first else {}
        self.reason = str(first.get("reason") or first_err.get("code") or "entity_loss")
        self.segment_index = int(first.get("index", first_err.get("segment_index", -1)))
        self.entity_name = str(first_err.get("entity_name") or "")
        self.entity_type = str(first_err.get("entity_type") or "")
        self.cause = str(first_err.get("cause") or self.reason)
        self.original_text = str(first_err.get("original_text") or "")
        self.changed_text = str(first_err.get("changed_text") or "")

    def format_diagnostic_block(self) -> str:
        lines = [
            f"SemanticValidationError · segment={self.segment_index} · reason={self.reason}",
        ]
        if self.entity_name:
            lines.append(f"Entity: {self.entity_name} ({self.entity_type})")
            lines.append(f"Cause: {self.cause}")
        if self.original_text:
            lines.append(f"Original: {self.original_text[:300]}")
        if self.changed_text:
            lines.append(f"Semantic output: {self.changed_text[:300]}")
        for fail in self.failures[:3]:
            chain = fail.get("transformation_chain") or {}
            lines.append("")
            lines.append(f"— Segment #{fail.get('index')} —")
            lines.append(
                f"Original English:  {str(chain.get('original') or chain.get('original_english') or '')[:200]}"
            )
            lines.append(f"Raw MT:            {str(chain.get('raw_mt') or '')[:200]}")
            lines.append(
                f"Semantic:          {str(chain.get('semantic') or chain.get('semantic_translation') or '')[:200]}"
            )
            lines.append(
                f"Final TTS:         {str(chain.get('final') or chain.get('final_tts_text') or '')[:200]}"
            )
            chain_details = fail.get("chain_details") or {}
            for reason in (
                chain_details.get("change_reasons") or fail.get("change_reasons") or []
            )[:5]:
                lines.append(
                    f"  · {reason.get('code', 'change')}: {reason.get('summary', '')}"
                )
            metrics = chain_details or {}
            if metrics.get("meaning_preservation_score") is not None:
                lines.append(
                    "  Metrics: "
                    f"meaning={metrics.get('meaning_preservation_score')} "
                    f"entity={metrics.get('entity_preservation_score')} "
                    f"naturalness={metrics.get('naturalness_score')} "
                    f"readability={metrics.get('readability_score')}"
                )
            if metrics.get("raw_mt_divergence") is not None:
                lines.append(
                    f"  Raw MT divergence (diagnostic only): {metrics.get('raw_mt_divergence')}"
                )
            for rep in (fail.get("entity_loss_reports") or [])[:5]:
                lines.append(
                    f"Lost Entity: {rep.get('lost_entity')} · module={rep.get('suspected_module')}"
                )
        runtime = self.details.get("runtime_pipeline") or {}
        if not runtime and self.details.get("pipeline_stages"):
            from engines.pipeline_integrity.semantic_validation_openddf import (
                build_runtime_pipeline,
            )

            runtime = build_runtime_pipeline(
                {"pipeline_stages": self.details.get("pipeline_stages")},
                validation_payload=self.details,
            )
        if runtime:
            from engines.pipeline_integrity.semantic_validation_openddf import (
                format_runtime_pipeline_block,
            )

            lines.append("")
            lines.append(format_runtime_pipeline_block(runtime))
        return "\n".join(lines)

    def to_openddf_exception_info(self) -> dict[str, Any]:
        first = self.failures[0] if self.failures else {}
        first_err = (first.get("errors") or [{}])[0] if first else {}
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": str(self),
            "reason": self.reason,
            "segment_index": self.segment_index,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "cause": self.cause,
            "location": first_err.get("location") or {},
            "original_text": self.original_text,
            "changed_text": self.changed_text,
            "final_text": first_err.get("final_text") or "",
            "suspected_module": first_err.get("suspected_module") or "",
            "problem_segment_indices": self.details.get("problem_segment_indices")
            or [],
        }


def pre_tts_quality_gate(
    source: str,
    text: str,
    *,
    original_translation: str = "",
    target_lang: str | None = None,
    predicted_ms: int = 0,
    slot_ms: int = 0,
) -> tuple[bool, str]:
    """Final gate before TTS (TZ §13)."""
    ok, reason, _ = verify_meaning_preserved(
        source,
        original_translation or text,
        text,
        target_lang=target_lang,
    )
    if not ok:
        return False, reason
    if is_truncated_adaptation(original_translation or text, text):
        return False, "truncated"
    if slot_ms > 0 and predicted_ms > int(slot_ms * 1.12):
        return False, "duration_overflow"
    return True, "ok"


_NUMBER_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?(?:%|st|nd|rd|th)?\b|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",
    re.I,
)
_ORG_CUE_RE = re.compile(
    r"\b(?:University|College|Institute|Corporation|Inc\.|Ltd\.|LLC|Foundation|"
    r"Department|Ministry|Hospital|Bank|Association|Organization|"
    r"Universität|Université|Universidad)\b",
    re.I,
)
_LOCATION_CUE_RE = re.compile(
    r"\b(?:Street|Avenue|Road|Boulevard|City|County|State|California|"
    r"Southern|Northern|Eastern|Western)\b",
    re.I,
)


def extract_meaning_units(text: str, *, source: str = "") -> list[dict[str, Any]]:
    """Semantic units for completeness checks (TZ §6, §8)."""
    src = str(source or text or "")
    t = str(text or "")
    units: list[dict[str, Any]] = []
    for tok in extract_preserved_tokens(src):
        units.append({"type": "named_entity", "value": tok})
    for m in _NUMBER_RE.finditer(src):
        units.append({"type": "number", "value": m.group(0)})
    for m in _ORG_CUE_RE.finditer(src):
        start = max(0, m.start() - 40)
        end = min(len(src), m.end() + 40)
        units.append({"type": "organization", "value": src[start:end].strip()[:80]})
    for m in _LOCATION_CUE_RE.finditer(src):
        start = max(0, m.start() - 30)
        end = min(len(src), m.end() + 30)
        units.append({"type": "location", "value": src[start:end].strip()[:80]})
    return units


def extract_critical_entity_tokens(source: str, *, app_dir=None) -> list[str]:
    """Named entities, abbreviations, numbers that must survive translation (TZ §6)."""
    from engines.translation_quality import extract_abbreviations, extract_proper_nouns

    src = str(source or "").strip()
    tokens: list[str] = []
    seen: set[str] = set()
    for tok in extract_proper_nouns(src) + extract_abbreviations(src):
        key = re.sub(r"\s+", "", tok.lower())
        if key in seen or len(tok) <= 2:
            continue
        if key in {"hello", "his", "her", "their", "this", "that"}:
            continue
        seen.add(key)
        tokens.append(tok)
    for m in _NUMBER_RE.finditer(src):
        num = m.group(0)
        if num not in tokens:
            tokens.append(num)
    return tokens


def _number_preserved(num: str, translated: str) -> bool:
    tr = str(translated or "")
    if num in tr:
        return True
    if num.replace(",", ".") in tr:
        return True
    if num.replace(".", ",") in tr:
        return True
    return False


def check_critical_entities(
    source: str,
    translated: str,
    *,
    app_dir=None,
    is_source_segment_incomplete: bool = False,  # New parameter
) -> list[dict[str, Any]]:
    """
    Hard entity check after Semantic Engine (TZ §6).
    Uses transliteration, alias dictionary, normalization — not literal match.
    """
    src = str(source or "").strip()
    tr = str(translated or "").strip()
    if not src or not tr:
        return []

    errors: list[dict[str, Any]] = []
    missing_names = missing_preserved_tokens(
        src,
        tr,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    )  # Pass new parameter
    for tok in missing_names:
        errors.append(
            {
                "code": "entity_missing",
                "category": "named_entity",
                "value": tok,
                "severity": "error",
                "reason": "not_found_after_normalization",
                "match_method": "transliteration_and_alias",
            }
        )

    for m in _NUMBER_RE.finditer(src):
        num = m.group(0)
        if not _number_preserved(num, tr):
            errors.append(
                {
                    "code": "entity_missing",
                    "category": "number",
                    "value": num,
                    "severity": "error",
                    "reason": "number_not_preserved",
                }
            )

    return errors


def compute_entity_preservation_score(
    source: str,
    translated: str,
    *,
    app_dir=None,
    is_source_segment_incomplete: bool = False,
) -> float:  # New parameter
    """1.0 = all critical entities preserved; 0.0 = major loss (TZ §9)."""
    src = str(source or "").strip()
    tr = str(translated or "").strip()
    if not src or not tr:
        return 1.0 if not src else 0.0

    tokens = _source_entity_tokens(src, app_dir=app_dir)
    if not tokens:
        return 1.0
    missing = missing_preserved_tokens(
        src,
        tr,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    )  # Pass new parameter
    kept = max(0, len(tokens) - len(missing))
    return round(kept / len(tokens), 3)


def is_truncated_vs_source(
    source: str,
    adapted: str,
    *,
    app_dir=None,
    is_source_segment_incomplete: bool = False,
) -> bool:  # New parameter
    """Detect meaning truncation vs Original English — not vs Raw MT."""
    src = str(source or "").strip()
    adpt = str(adapted or "").strip()
    if not adpt:
        return True
    if _ELLIPSIS_END.search(adpt):
        return True

    tokens = _source_entity_tokens(src, app_dir=app_dir)
    if tokens:
        missing = missing_preserved_tokens(
            src,
            adpt,
            app_dir=app_dir,
            is_source_segment_incomplete=is_source_segment_incomplete,
        )  # Pass new parameter
        if len(missing) / len(tokens) > 0.4:
            return True

    nums_src = [m.group(0) for m in _NUMBER_RE.finditer(src)]
    if nums_src:
        nums_missing = sum(1 for n in nums_src if not _number_preserved(n, adpt))
        if nums_missing / len(nums_src) > 0.5:
            return True

    if (
        adpt[-1] not in ".!?…"
        and word_count(adpt) >= 6
        and _INCOMPLETE_END.search(adpt)
    ):
        return True
    return False


def compute_fact_preservation_score(source: str, translated: str) -> float:
    """1.0 = all numbers/dates preserved; 0.0 = major fact loss."""
    src = str(source or "").strip()
    tr = str(translated or "").strip()
    if not src:
        return 1.0
    if not tr:
        return 0.0
    nums = [m.group(0) for m in _NUMBER_RE.finditer(src)]
    if not nums:
        return 1.0
    kept = sum(1 for n in nums if _number_preserved(n, tr))
    return round(kept / len(nums), 3)


def compute_meaning_preservation_score(
    source: str,
    adapted: str,
    *,
    app_dir=None,
) -> float:
    """1.0 = full meaning preserved vs Original English."""
    return round(
        1.0 - compute_meaning_loss_score(source, "", adapted, app_dir=app_dir), 3
    )


def compute_naturalness_score(raw_mt: str, semantic: str) -> float:
    """Higher when Semantic Engine improves phrasing vs Raw MT (diagnostic + metric)."""
    raw = str(raw_mt or "").strip()
    sem = str(semantic or "").strip()
    if not sem:
        return 0.0
    if not raw or raw == sem:
        return 0.55

    score = 0.45
    calque_patterns = (
        r"\bне\s+м[іi]г\s+не\s+",
        r"\bне\s+мог\s+не\s+",
        r"\bcould\s+not\s+help\s+but\b",
        r"\breally\s+dreading\b",
    )
    for pat in calque_patterns:
        if re.search(pat, raw, re.I) and not re.search(pat, sem, re.I):
            score += 0.12

    raw_w = word_count(raw)
    sem_w = word_count(sem)
    if raw_w >= 6 and sem_w >= int(raw_w * 0.55):
        score += 0.15

    if sem_w < raw_w and raw_w >= 8:
        score += 0.08

    return round(min(1.0, score), 3)


def compute_readability_score(text: str) -> float:
    """Spoken-text readability heuristic."""
    t = str(text or "").strip()
    if not t:
        return 0.0
    score = 0.65
    if t[-1] in ".!?…":
        score += 0.15
    wc = word_count(t)
    if 3 <= wc <= 40:
        score += 0.1
    if _ELLIPSIS_END.search(t) or _INCOMPLETE_END.search(t):
        score -= 0.35
    return round(max(0.0, min(1.0, score)), 3)


def compute_compression_ratio(raw_mt: str, semantic: str) -> float | None:
    """Word-count ratio semantic/raw_mt — diagnostic only."""
    rw = word_count(raw_mt)
    if rw <= 0:
        return None
    return round(word_count(semantic) / rw, 3)


def infer_semantic_change_reasons(
    source: str,
    raw_mt: str,
    semantic: str,
) -> list[dict[str, str]]:
    """Explain WHY Semantic Engine changed wording (OpenDDF diagnostics)."""
    raw = str(raw_mt or "").strip()
    sem = str(semantic or "").strip()
    if not sem or not raw or raw == sem:
        return []

    reasons: list[dict[str, str]] = []
    compression = compute_compression_ratio(raw, sem)
    if compression is not None and compression < 0.85:
        reasons.append(
            {
                "code": "timing_compression",
                "summary": "Shortened phrasing for speech timing while preserving meaning",
            }
        )

    calque_checks = (
        (
            r"\bне\s+м[іi]г\s+не\s+",
            "idiom_replacement",
            "Replaced literal calque with natural idiom",
        ),
        (
            r"\bне\s+мог\s+не\s+",
            "idiom_replacement",
            "Replaced literal calque with natural idiom",
        ),
        (
            r"\bcould\s+not\s+help\s+but\b",
            "idiom_replacement",
            "Replaced English calque pattern",
        ),
    )
    for pat, code, summary in calque_checks:
        if re.search(pat, raw, re.I) and not re.search(pat, sem, re.I):
            reasons.append({"code": code, "summary": summary})
            break

    raw_tokens = set(re.findall(r"\b[\w'-]+\b", raw.lower()))
    sem_tokens = set(re.findall(r"\b[\w'-]+\b", sem.lower()))
    overlap = len(raw_tokens & sem_tokens) / max(len(raw_tokens), 1)
    if overlap < 0.45:
        reasons.append(
            {
                "code": "grammar_restructuring",
                "summary": "Restructured grammar for natural spoken flow",
            }
        )
    elif overlap < 0.75:
        reasons.append(
            {
                "code": "naturalness_improvement",
                "summary": "Improved natural spoken phrasing",
            }
        )

    if compression is not None and compression < 0.75:
        reasons.append(
            {
                "code": "literal_translation_removal",
                "summary": "Removed redundant literal translation wording",
            }
        )

    if str(source or "").strip() and not reasons:
        reasons.append(
            {
                "code": "literary_style",
                "summary": "Literary/natural paraphrase while preserving meaning vs original",
            }
        )
    return reasons


def compute_semantic_validation_metrics(
    source: str,
    raw_mt: str,
    semantic: str,
    final_tts: str,
    *,
    app_dir=None,
) -> dict[str, Any]:
    """Aggregate validation metrics — primary reference is Original English."""
    src = str(source or "").strip()
    raw = str(raw_mt or "").strip()
    sem = str(semantic or "").strip()
    final = str(final_tts or semantic or "").strip()

    meaning_loss = compute_meaning_loss_score(src, "", final, app_dir=app_dir)
    meaning_preservation = round(1.0 - meaning_loss, 3)
    entity_preservation = compute_entity_preservation_score(src, final, app_dir=app_dir)
    fact_preservation = compute_fact_preservation_score(src, final)
    naturalness = compute_naturalness_score(raw, sem or final)
    readability = compute_readability_score(final)
    compression = compute_compression_ratio(raw, sem or final)
    change_reasons = infer_semantic_change_reasons(src, raw, sem or final)

    aggregate_score = round(
        meaning_preservation * 0.35
        + entity_preservation * 0.25
        + fact_preservation * 0.20
        + naturalness * 0.10
        + readability * 0.10,
        3,
    )

    raw_mt_divergence = None
    if raw and (sem or final):
        raw_tokens = set(re.findall(r"\b[\w'-]+\b", raw.lower()))
        out_tokens = set(re.findall(r"\b[\w'-]+\b", (sem or final).lower()))
        if raw_tokens:
            raw_mt_divergence = round(
                1.0 - len(raw_tokens & out_tokens) / len(raw_tokens),
                3,
            )

    return {
        "meaning_preservation_score": meaning_preservation,
        "meaning_loss_score": meaning_loss,
        "entity_preservation_score": entity_preservation,
        "fact_preservation_score": fact_preservation,
        "naturalness_score": naturalness,
        "readability_score": readability,
        "compression_ratio": compression,
        "aggregate_score": aggregate_score,
        "raw_mt_divergence": raw_mt_divergence,
        "change_reasons": change_reasons,
        "reference_hierarchy": {
            "primary": "original_english",
            "diagnostic_only": "raw_mt",
        },
    }


def compute_meaning_loss_score(
    source: str,
    baseline: str,
    adapted: str,
    *,
    app_dir=None,
    is_source_segment_incomplete: bool = False,  # New parameter
) -> float:
    """
    0.0 = no loss; 1.0 = total loss.
    Evaluates meaning preservation vs Original English only.
    ``baseline`` (Raw MT) is ignored — kept for call-site compatibility.
    """
    src = str(source or "").strip()
    adpt = str(adapted or "").strip()
    if not adpt:
        return 1.0
    if not src:
        return 0.0

    loss = 0.0

    entity_score = compute_entity_preservation_score(
        src,
        adpt,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    )  # Pass new parameter
    loss = max(loss, 1.0 - entity_score)

    fact_score = compute_fact_preservation_score(src, adpt)
    loss = max(loss, 1.0 - fact_score)

    missing = missing_preserved_tokens(
        src,
        adpt,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    )  # Pass new parameter
    if missing:
        loss = max(loss, min(1.0, 0.5 + 0.1 * len(missing)))

    if is_truncated_vs_source(
        src,
        adpt,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    ):  # Pass new parameter
        loss = max(loss, 0.85)

    if adpt[-1] not in ".!?…" and word_count(adpt) >= 6:
        loss = max(loss, 0.2)

    return round(min(1.0, loss), 3)


def meaning_loss_risk(score: float) -> str:
    if score >= 0.35:
        return "HIGH"
    if score >= 0.15:
        return "MEDIUM"
    return "LOW"


def validate_transformation_chain(
    *,
    original: str,
    raw_mt: str,
    semantic: str,
    final_tts: str,
    source: str = "",
    max_loss_ratio: float = 0.10,
    app_dir=None,
    is_source_segment_incomplete: bool = False,  # New parameter
) -> tuple[bool, str, dict[str, Any]]:
    """
    Post-Semantic check: compare Semantic output vs Original English.
    Raw MT is diagnostic only — divergence from Raw MT is NOT an error.
    """
    src = str(source or original or "").strip()
    raw = str(raw_mt or "").strip()
    sem = str(semantic or "").strip()
    final = str(final_tts or semantic or "").strip()

    metrics = compute_semantic_validation_metrics(src, raw, sem, final, app_dir=app_dir)
    entity_errors = check_critical_entities(
        src,
        final,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    )  # Pass new parameter
    details: dict[str, Any] = {
        "original_words": word_count(original),
        "raw_mt_words": word_count(raw_mt),
        "semantic_words": word_count(semantic),
        "final_words": word_count(final),
        "entity_errors": entity_errors,
        "transformation_chain": {
            "original_english": src,
            "raw_mt": raw,
            "semantic_translation": sem,
            "final_tts_text": final,
        },
        **metrics,
    }

    if not final:
        return False, "empty_output", details

    if entity_errors:
        names = [e["value"] for e in entity_errors[:5]]
        return False, "entity_loss", {**details, "missing_entities": names}

    score = float(metrics["meaning_loss_score"])
    if score > max_loss_ratio:
        return False, "meaning_loss_exceeded", details

    if is_truncated_vs_source(
        src,
        final,
        app_dir=app_dir,
        is_source_segment_incomplete=is_source_segment_incomplete,
    ):  # Pass new parameter
        return False, "truncated_final", details

    return True, "ok", details


def should_prefer_semantic_over_raw_mt(
    *,
    semantic: str,
    raw_mt: str,
    source: str = "",
    fail_reason: str = "",
    app_dir=None,
) -> bool:
    """Keep semantic polish when raw MT is a shorter split fragment with worse coverage."""
    sem = str(semantic or "").strip()
    raw = str(raw_mt or "").strip()
    if not sem:
        return False
    if not raw:
        return True

    sem_wc = word_count(sem)
    raw_wc = word_count(raw)
    if sem_wc >= 16 and raw_wc < max(10, int(sem_wc * 0.55)):
        return True

    src = str(source or "").strip()
    if src:
        sem_loss = compute_meaning_loss_score(src, "", sem, app_dir=app_dir)
        raw_loss = compute_meaning_loss_score(src, "", raw, app_dir=app_dir)
        if raw_loss > sem_loss + 0.08:
            return True

    reason = str(fail_reason or "").strip().lower()
    if reason in ("entity_loss", "truncated_final", "meaning_loss_exceeded") and raw_wc < sem_wc:
        return True
    return False
