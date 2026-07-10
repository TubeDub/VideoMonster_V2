"""Pre-compute Router routes — prepare uses direct route only."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang, pair_key


@dataclass
class MTEngineRequirement:
    engine_id: str
    src: str
    tgt: str
    required: bool = True

    @property
    def variant(self) -> str:
        return f"{self.engine_id}:{self.src}-{self.tgt}"

    @property
    def pair_variant(self) -> str:
        return f"{self.src}-{self.tgt}"


@dataclass
class TranslationRoutePlan:
    source_lang: str
    target_lang: str
    primary_route: str = ""
    route_labels: list[str] = field(default_factory=list)
    reserve_route_labels: list[str] = field(default_factory=list)
    prepare_legs: list[tuple[str, str]] = field(default_factory=list)
    legs: list[tuple[str, str]] = field(default_factory=list)
    mt_requirements: list[MTEngineRequirement] = field(default_factory=list)
    cascade_by_leg: dict[str, list[str]] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "primary_route": self.primary_route,
            "prepare_route": self.primary_route,
            "prepare_legs": [f"{s}→{t}" for s, t in self.prepare_legs],
            "reserve_routes": self.reserve_route_labels,
            "all_routes": self.route_labels,
            "mt_prepare": [
                {
                    "engine": r.engine_id,
                    "pair": f"{r.src}→{r.tgt}",
                    "required": r.required,
                }
                for r in self.mt_requirements
            ],
        }

    def to_dev_log_lines(self) -> list[str]:
        lines = [
            f"pair={self.source_lang}→{self.target_lang}",
            f"primary_route={self.primary_route}",
            "",
            "=== PREPARE (direct route only) ===",
            f"legs={[f'{s}→{t}' for s, t in self.prepare_legs]}",
        ]
        for req in self.mt_requirements:
            tag = "required" if req.required else "optional"
            lines.append(f"  mt {req.engine_id} {req.src}→{req.tgt} [{tag}]")
        lines.extend(
            [
                "",
                "=== RESERVE ROUTES (runtime fallback, not pre-downloaded) ===",
            ]
        )
        for lbl in self.reserve_route_labels[:12]:
            lines.append(f"  {lbl}")
        if len(self.reserve_route_labels) > 12:
            lines.append(f"  ... +{len(self.reserve_route_labels) - 12} more")
        lines.extend(["", "=== CASCADE BY PREPARE LEG ==="])
        for leg, engines in self.cascade_by_leg.items():
            lines.append(f"  {leg}: {', '.join(engines)}")
        return lines


def _engines_for_prepare(app_dir: Path, leg_src: str, leg_tgt: str) -> list[tuple[str, bool]]:
    from engines.mt.registry import engines_for_pair

    primary, fallback = engines_for_pair(app_dir, leg_src, leg_tgt)
    out = [(primary, True)]
    if fallback and fallback != primary:
        out.append((fallback, False))
    return out


def plan_translation_requirements(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
) -> TranslationRoutePlan:
    """
    Build prepare plan: direct route legs only + MT cascade per leg.
    Pivot/reserve routes are runtime-only (not scanned here — avoids 30s+ router pass).
    """
    src = normalize_lang(source_lang or "en")
    tgt = normalize_lang(target_lang or "ru")
    plan = TranslationRoutePlan(source_lang=src, target_lang=tgt)

    if src == tgt:
        return plan

    plan.primary_route = f"{src}→{tgt}"
    plan.prepare_legs = [(src, tgt)]
    plan.route_labels = [plan.primary_route]
    plan.legs = list(plan.prepare_legs)

    try:
        from engines.translation_router import load_fallback_routes

        fb = load_fallback_routes(app_dir).get(pair_key(src, tgt))
        if fb:
            plan.reserve_route_labels = [f"fallback:{src}→{tgt}"]
    except Exception:
        pass

    seen_mt: set[tuple[str, str, str]] = set()
    for leg_src, leg_tgt in plan.prepare_legs:
        engines = _engines_for_prepare(app_dir, leg_src, leg_tgt)
        plan.cascade_by_leg[f"{leg_src}→{leg_tgt}"] = [e for e, _ in engines]

        for eng_id, required in engines:
            if eng_id == "argos":
                from engines.model_manager.downloader import argos_pair_available

                if not argos_pair_available(leg_src, leg_tgt):
                    continue
                required = False
            key = (eng_id, leg_src, leg_tgt)
            if key in seen_mt:
                continue
            seen_mt.add(key)
            plan.mt_requirements.append(
                MTEngineRequirement(eng_id, leg_src, leg_tgt, required=required)
            )

    return plan
