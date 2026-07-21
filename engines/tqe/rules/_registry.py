"""Rule registration (no side-effect imports)."""

from __future__ import annotations

from typing import Callable

RuleFn = Callable[[str, str, dict], list[dict]]

_REGISTRY: dict[str, RuleFn] = {}


def register(name: str):
    def deco(fn: RuleFn):
        _REGISTRY[name] = fn
        return fn

    return deco


def get_rule(name: str) -> RuleFn | None:
    return _REGISTRY.get(name)


def load_all_rules() -> dict[str, RuleFn]:
    from engines.tqe.rules import (  # noqa: F401
        dates,
        entity,
        grammar,
        hallucination,
        meaning,
        numbers,
        quotes,
        sentence,
        timing,
    )

    return dict(_REGISTRY)
