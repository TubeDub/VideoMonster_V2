"""Versioned contracts — Master Spec Part 1 Foundations v6.0.

Required contracts:
  Recognition, Sentence, Translation, Dub, Scheduler, Alignment, Merge, Studio
Plus TTS (operational audio generation).

Any version mismatch → ContractVersionError.
"""

from __future__ import annotations

from typing import Any

from engines.pipeline_integrity.exceptions import ContractVersionError

RECOGNITION_CONTRACT_VERSION: int = 1
SENTENCE_CONTRACT_VERSION: int = 1
TRANSLATION_CONTRACT_VERSION: int = 1
DUB_CONTRACT_VERSION: int = 1
SCHEDULER_CONTRACT_VERSION: int = 1
ALIGNMENT_CONTRACT_VERSION: int = 1
MERGE_CONTRACT_VERSION: int = 1
STUDIO_CONTRACT_VERSION: int = 1
TTS_CONTRACT_VERSION: int = 1

CONTRACT_META: dict[str, dict[str, Any]] = {
    "recognition_contract_version": {
        "version": RECOGNITION_CONTRACT_VERSION,
        "owner": "Recognition",
        "description": "ASR / Words — Whisper timestamps only",
        "compatibility": ">=1",
        "migration": "Bump when Word schema fields change",
    },
    "sentence_contract_version": {
        "version": SENTENCE_CONTRACT_VERSION,
        "owner": "Semantic Layer",
        "description": "SemanticSentence identity and meaning graph",
        "compatibility": ">=1",
        "migration": "Bump when Sentence Builder output schema changes",
    },
    "translation_contract_version": {
        "version": TRANSLATION_CONTRACT_VERSION,
        "owner": "Translation Engine",
        "description": "Translated / locked meaning payload",
        "compatibility": ">=1",
        "migration": "Bump when locked text fields change",
    },
    "dub_contract_version": {
        "version": DUB_CONTRACT_VERSION,
        "owner": "Dub Engine",
        "description": "SpeechUnit preparation for TTS",
        "compatibility": ">=1",
        "migration": "Bump when SpeechUnit schema changes",
    },
    "scheduler_contract_version": {
        "version": SCHEDULER_CONTRACT_VERSION,
        "owner": "Scheduler",
        "description": "Timeline / AudioUnit placement",
        "compatibility": ">=1",
        "migration": "Bump when timing field set changes",
    },
    "alignment_contract_version": {
        "version": ALIGNMENT_CONTRACT_VERSION,
        "owner": "Dub Engine",
        "description": "Word/phoneme/viseme alignment to audio",
        "compatibility": ">=1",
        "migration": "Bump when alignment artifact schema changes",
    },
    "merge_contract_version": {
        "version": MERGE_CONTRACT_VERSION,
        "owner": "Merge Engine",
        "description": "Final mix without text mutation",
        "compatibility": ">=1",
        "migration": "Bump when merge manifest schema changes",
    },
    "studio_contract_version": {
        "version": STUDIO_CONTRACT_VERSION,
        "owner": "Studio",
        "description": "Studio handoff / export surface",
        "compatibility": ">=1",
        "migration": "Bump when Studio API payload changes",
    },
    "tts_contract_version": {
        "version": TTS_CONTRACT_VERSION,
        "owner": "TTS Engine",
        "description": "Audio generation — never mutates text",
        "compatibility": ">=1",
        "migration": "Bump when TTS artifact metadata changes",
    },
}

CONTRACT_VERSIONS: dict[str, int] = {
    key: int(meta["version"]) for key, meta in CONTRACT_META.items()
}

CONTRACT_VERSION_KEYS: tuple[str, ...] = tuple(CONTRACT_VERSIONS.keys())

# Legacy keys that must remain present for older callers
LEGACY_REQUIRED_KEYS: tuple[str, ...] = (
    "translation_contract_version",
    "dub_contract_version",
    "scheduler_contract_version",
    "studio_contract_version",
    "tts_contract_version",
)


def stamp_contract_versions(container: dict[str, Any]) -> dict[str, int]:
    """Write all contract versions. Idempotent if equal; mismatch raises."""
    for key, expected in CONTRACT_VERSIONS.items():
        existing = container.get(key)
        if existing is not None and int(existing) != expected:
            raise ContractVersionError(
                f"{key} mismatch: have {existing}, expected {expected}",
                details={"field": key, "have": existing, "expected": expected},
            )
        container[key] = expected
    return {k: int(container[k]) for k in CONTRACT_VERSION_KEYS}


def require_contract_versions(
    container: dict[str, Any] | None,
    *,
    keys: tuple[str, ...] | None = None,
    full: bool = False,
) -> dict[str, int]:
    """Require contract stamps. Default = legacy 5 keys; ``full=True`` = Part 1 set."""
    info = container or {}
    required = keys or (CONTRACT_VERSION_KEYS if full else LEGACY_REQUIRED_KEYS)
    missing = [k for k in required if info.get(k) is None]
    if missing:
        raise ContractVersionError(
            f"missing contract versions: {', '.join(missing)}",
            details={"missing": missing},
        )
    out: dict[str, int] = {}
    for key in required:
        expected = CONTRACT_VERSIONS[key]
        have = int(info[key])
        if have != expected:
            raise ContractVersionError(
                f"unsupported {key}: {have}",
                details={"have": have, "expected": expected},
            )
        out[key] = have
    return out


def contract_catalog() -> dict[str, dict[str, Any]]:
    """Part 1 — version, compatibility, description, migration per contract."""
    return {k: dict(v) for k, v in CONTRACT_META.items()}
