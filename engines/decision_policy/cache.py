"""P314 Decision Cache — deterministic, contract-aware."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from engines.decision_policy.types import DecisionRecord, StrategyCandidate

_CACHE: dict[str, dict[str, Any]] = {}


def _fingerprint(
    *,
    problem: str,
    profile: str,
    overflow_ms: int,
    slot_ms: int,
    contract_version: int,
    settings_hash: str,
) -> str:
    payload = "|".join(
        [
            problem,
            profile,
            str(overflow_ms),
            str(slot_ms),
            str(contract_version),
            settings_hash,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def settings_hash(cfg: dict[str, Any]) -> str:
    blob = json.dumps(
        {"costs": cfg.get("costs"), "weights": cfg.get("score_weights"), "v": cfg.get("version")},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cache_get(
    *,
    problem: str,
    profile: str,
    overflow_ms: int,
    slot_ms: int,
    cfg: dict[str, Any],
) -> DecisionRecord | None:
    cache_cfg = cfg.get("cache") or {}
    if not cache_cfg.get("enabled", True):
        return None
    key = _fingerprint(
        problem=problem,
        profile=profile,
        overflow_ms=overflow_ms,
        slot_ms=slot_ms,
        contract_version=int(cfg.get("version") or 1),
        settings_hash=settings_hash(cfg),
    )
    raw = _CACHE.get(key)
    if not raw:
        return None
    acc = raw.get("accepted") or {}
    accepted = StrategyCandidate(
        strategy_id=str(acc.get("strategy_id") or ""),
        label=str(acc.get("label") or ""),
        steps=list(acc.get("steps") or []),
        cost=float(acc.get("cost") or 0),
        scores=dict(acc.get("scores") or {}),
        decision_score=float(acc.get("decision_score") or 0),
        explanation=str(acc.get("explanation") or ""),
        expected_fit=bool(acc.get("expected_fit")),
    )
    rec = DecisionRecord(
        sentence_uuid=str(raw.get("sentence_uuid") or ""),
        problem=problem,
        profile=profile,
        accepted=accepted,
        reason=str(raw.get("reason") or "cache_hit"),
        cached=True,
        confidences=dict(raw.get("confidences") or {}),
    )
    return rec


def cache_put(record: DecisionRecord, *, cfg: dict[str, Any], overflow_ms: int, slot_ms: int) -> None:
    cache_cfg = cfg.get("cache") or {}
    if not cache_cfg.get("enabled", True):
        return
    if not record.accepted:
        return
    key = _fingerprint(
        problem=record.problem,
        profile=record.profile,
        overflow_ms=overflow_ms,
        slot_ms=slot_ms,
        contract_version=int(cfg.get("version") or 1),
        settings_hash=settings_hash(cfg),
    )
    _CACHE[key] = record.to_dict()


def clear_decision_cache() -> None:
    _CACHE.clear()
