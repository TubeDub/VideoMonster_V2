"""Peer Validation — each downstream agent validates upstream output contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from engines.mt.lang_codes import normalize_lang
from engines.pipeline_language_gate import is_critical_language_mismatch

# Downstream agent → immediate upstream agent (pipeline order).
PEER_UPSTREAM: dict[str, str] = {
    "semantic": "translation",
    "timing": "semantic",
    "grammar": "timing",
    "quality": "grammar",
    "reviewer": "quality",
    "voice_preparation": "reviewer",
    "voice": "voice_preparation",
    "voice_verification": "voice",
    "mix": "voice_verification",
}

MAX_PEER_RETURNS = 3


@dataclass
class PeerReturn:
    """Segment returned to upstream agent — diagnostic record."""

    segment_index: int
    receiver_agent: str
    source_agent: str
    reason: str
    error_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_index": self.segment_index,
            "receiver_agent": self.receiver_agent,
            "source_agent": self.source_agent,
            "reason": self.reason,
            "error_code": self.error_code,
        }


@dataclass
class PeerValidationResult:
    ok: bool
    returns: list[PeerReturn] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "returns": [r.to_dict() for r in self.returns],
            "warnings": self.warnings,
        }


def _non_empty(field: str, code: str) -> Callable[..., PeerReturn | None]:
    def _check(
        agent: str,
        upstream: str,
        seg: dict[str, Any],
        *,
        target_lang: str = "uk",
        manifest: dict[str, Any] | None = None,
    ) -> PeerReturn | None:
        idx = int(seg.get("index", 0))
        val = str(seg.get(field) or "").strip()
        if not val:
            return PeerReturn(
                segment_index=idx,
                receiver_agent=upstream,
                source_agent=agent,
                reason=f"missing or empty {field}",
                error_code=code,
            )
        return None

    return _check


def _check_target_language(
    agent: str,
    upstream: str,
    seg: dict[str, Any],
    *,
    target_lang: str = "uk",
    manifest: dict[str, Any] | None = None,
) -> PeerReturn | None:
    """Translation output must be in target language (not source leak)."""
    idx = int(seg.get("index", 0))
    translated = str(seg.get("translated_text") or "").strip()
    source = str(seg.get("text") or "").strip()
    if not translated:
        return None
    bad, code = is_critical_language_mismatch(
        translated, target_lang=target_lang, original=source
    )
    if bad:
        return PeerReturn(
            segment_index=idx,
            receiver_agent=upstream,
            source_agent=agent,
            reason=f"translation language mismatch: {code}",
            error_code=code or "language_mismatch",
        )
    return None


def _check_semantic_meaning(
    agent: str,
    upstream: str,
    seg: dict[str, Any],
    *,
    target_lang: str = "uk",
    manifest: dict[str, Any] | None = None,
) -> PeerReturn | None:
    """Grammar peer-check: upstream semantic adaptation preserved basic meaning."""
    idx = int(seg.get("index", 0))
    source = str(seg.get("text") or "").strip()
    semantic = str(seg.get("semantic_text") or "").strip()
    if not source or not semantic:
        return None
    try:
        from engines.dub_quality_stabilization import basic_meaning_preserved

        if not basic_meaning_preserved(source, semantic):
            return PeerReturn(
                segment_index=idx,
                receiver_agent="semantic",
                source_agent=agent,
                reason="semantic meaning not preserved for grammar input",
                error_code="meaning_loss",
            )
    except Exception:
        pass
    return None


def _check_timing_text_usable(
    agent: str,
    upstream: str,
    seg: dict[str, Any],
    *,
    target_lang: str = "uk",
    manifest: dict[str, Any] | None = None,
) -> PeerReturn | None:
    """Voice prep: timing/grammar text must exist for TTS."""
    idx = int(seg.get("index", 0))
    text = str(
        seg.get("grammar_text")
        or seg.get("timing_text")
        or seg.get("semantic_text")
        or ""
    ).strip()
    if not text:
        return PeerReturn(
            segment_index=idx,
            receiver_agent=upstream,
            source_agent=agent,
            reason="no text available for voice preparation",
            error_code="missing_tts_text",
        )
    return None


def _check_voice_audio(
    agent: str,
    upstream: str,
    seg: dict[str, Any],
    *,
    target_lang: str = "uk",
    manifest: dict[str, Any] | None = None,
) -> PeerReturn | None:
    """Mix peer-check: voice segment WAV exists."""
    idx = int(seg.get("index", 0))
    wav = str(seg.get("wav_path") or seg.get("audio_path") or "").strip()
    if not wav:
        return PeerReturn(
            segment_index=idx,
            receiver_agent=upstream,
            source_agent=agent,
            reason="voice audio file missing",
            error_code="missing_voice_audio",
        )
    return None


# Per-agent input contract checks — only what the agent needs to start work.
PEER_SEGMENT_CHECKS: dict[str, list[Callable[..., PeerReturn | None]]] = {
    "semantic": [
        _non_empty("translated_text", "missing_translation"),
        _check_target_language,
    ],
    "timing": [
        _non_empty("semantic_text", "missing_semantic_text"),
    ],
    "grammar": [
        _non_empty("timing_text", "missing_timing_text"),
        _check_semantic_meaning,
    ],
    "quality": [
        _non_empty("grammar_text", "missing_grammar_text"),
    ],
    "reviewer": [
        _non_empty("grammar_text", "missing_grammar_text"),
    ],
    "voice_preparation": [
        _check_timing_text_usable,
    ],
    "voice": [
        _check_timing_text_usable,
    ],
    "voice_verification": [
        _check_timing_text_usable,
    ],
    "mix": [],
}


def validate_segment_peer_input(
    agent: str,
    seg: dict[str, Any],
    *,
    target_lang: str = "uk",
    manifest: dict[str, Any] | None = None,
) -> list[PeerReturn]:
    """Validate one segment against downstream agent input contract."""
    checks = PEER_SEGMENT_CHECKS.get(agent) or []
    if not checks:
        return []
    upstream = PEER_UPSTREAM.get(agent, "")
    returns: list[PeerReturn] = []
    for check in checks:
        hit = check(agent, upstream, seg, target_lang=target_lang, manifest=manifest)
        if hit:
            returns.append(hit)
            break
    return returns


def validate_upstream_batch(
    agent: str,
    segments: list[dict[str, Any]],
    *,
    target_lang: str = "uk",
    manifest: dict[str, Any] | None = None,
    upstream_status: str = "success",
) -> PeerValidationResult:
    """Validate all segments before agent runs. Checks upstream status + per-segment contract."""
    warnings: list[str] = []
    if upstream_status == "error":
        upstream = PEER_UPSTREAM.get(agent, "upstream")
        return PeerValidationResult(
            ok=False,
            returns=[
                PeerReturn(
                    segment_index=-1,
                    receiver_agent=upstream,
                    source_agent=agent,
                    reason=f"{upstream}_agent_status=error",
                    error_code="upstream_agent_failed",
                )
            ],
        )
    if upstream_status == "warning":
        warnings.append(f"{PEER_UPSTREAM.get(agent, 'upstream')}_agent_warning")

    if not segments and agent in PEER_SEGMENT_CHECKS:
        upstream = PEER_UPSTREAM.get(agent, "upstream")
        return PeerValidationResult(
            ok=False,
            returns=[
                PeerReturn(
                    segment_index=-1,
                    receiver_agent=upstream,
                    source_agent=agent,
                    reason="no segments in state",
                    error_code="no_segments",
                )
            ],
        )

    all_returns: list[PeerReturn] = []
    tgt = normalize_lang(target_lang or (manifest or {}).get("target_lang") or "uk")
    for seg in segments:
        if seg.get("merged_into") is not None:
            continue
        all_returns.extend(
            validate_segment_peer_input(agent, seg, target_lang=tgt, manifest=manifest)
        )

    return PeerValidationResult(
        ok=len(all_returns) == 0,
        returns=all_returns,
        warnings=warnings,
    )


def upstream_status_field(agent: str) -> str:
    """State field name for upstream agent status."""
    upstream = PEER_UPSTREAM.get(agent)
    if not upstream:
        return ""
    return f"{upstream}_agent_status"


def route_agent_for_return(peer_return: PeerReturn) -> str:
    """Agent to re-run for this peer return."""
    return peer_return.receiver_agent


_RETRY_AGENT_CLASS: dict[str, str] = {
    "translation": "TranslationAgent",
    "semantic": "SemanticAgent",
    "timing": "TimingAgent",
    "grammar": "GrammarAgent",
}


def retry_agent_class_name(agent_slug: str) -> str:
    return _RETRY_AGENT_CLASS.get(agent_slug, agent_slug)

