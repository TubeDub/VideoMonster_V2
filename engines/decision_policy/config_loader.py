"""Load Decision Policy configuration (costs + profiles) — never hardcode weights."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT = Path(__file__).resolve().parent / "config" / "default_policy.json"


def _overlay_path() -> Path | None:
    env = os.environ.get("VM_DECISION_POLICY_CONFIG", "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
    # Optional project data overlay
    root = Path(__file__).resolve().parents[2]
    cand = root / "data" / "decision_policy.json"
    return cand if cand.is_file() else None


@lru_cache(maxsize=4)
def load_policy_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else (_overlay_path() or _DEFAULT)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("decision policy config must be an object")
    if "costs" not in data or "profiles" not in data:
        raise ValueError("decision policy config requires costs and profiles")
    return data


def clear_policy_cache() -> None:
    load_policy_config.cache_clear()


def get_costs(cfg: dict[str, Any] | None = None) -> dict[str, float]:
    c = (cfg or load_policy_config()).get("costs") or {}
    return {str(k): float(v) for k, v in c.items()}


def get_profile(name: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    profiles = (cfg or load_policy_config()).get("profiles") or {}
    key = name if name in profiles else "Movie"
    if key not in profiles:
        # First available
        key = next(iter(profiles))
    return deepcopy(profiles[key])


def list_profiles(cfg: dict[str, Any] | None = None) -> list[str]:
    return sorted((cfg or load_policy_config()).get("profiles") or {})


def get_score_weights(cfg: dict[str, Any] | None = None) -> dict[str, float]:
    w = (cfg or load_policy_config()).get("score_weights") or {}
    return {str(k): float(v) for k, v in w.items()}


def min_strategies(cfg: dict[str, Any] | None = None) -> int:
    return max(4, int((cfg or load_policy_config()).get("min_strategies") or 4))


def strategy_cost(steps: list[str], cfg: dict[str, Any] | None = None) -> float:
    costs = get_costs(cfg)
    return float(sum(costs.get(s, 10.0) for s in steps if s != "ready"))
