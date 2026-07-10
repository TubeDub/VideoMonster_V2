"""Language-pair → required components (direct route prepare only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engines.model_manager.route_planner import TranslationRoutePlan, plan_translation_requirements


@dataclass
class ProfileItem:
    component_id: str
    variant: str
    weight: float = 1.0
    engine_id: str = ""
    src_lang: str = ""
    tgt_lang: str = ""


def profile_for_pair(
    app_dir: Path,
    source_lang: str,
    target_lang: str,
    *,
    whisper_size: str = "tiny",
    ocr_enabled: bool = False,
    feature: str = "dub",
) -> list[ProfileItem]:
    from engines.mt.lang_codes import normalize_lang

    src = normalize_lang(source_lang or "en")
    tgt = normalize_lang(target_lang or "ru")
    feat = (feature or "dub").strip().lower()
    items: list[ProfileItem] = []

    if feat in ("dub", "stt", "translate_stt"):
        items.append(ProfileItem("whisper", whisper_size, 3.0))

    if feat in ("dub", "translate", "translate_stt") and src != tgt:
        plan = plan_translation_requirements(app_dir, src, tgt)
        for leg_src, leg_tgt in plan.prepare_legs:
            pair_var = f"{leg_src}-{leg_tgt}"
            items.append(
                ProfileItem(
                    "mt",
                    pair_var,
                    4.0 / max(len(plan.prepare_legs), 1),
                    src_lang=leg_src,
                    tgt_lang=leg_tgt,
                )
            )
        items.append(ProfileItem("naturalizer", tgt, 0.1))

    if feat in ("dub", "tts"):
        items.append(ProfileItem("tts", tgt, 0.1))

    if ocr_enabled and feat == "dub":
        items.append(ProfileItem("ocr", "default", 1.0))

    return items


def route_plan_for_pair(app_dir: Path, source_lang: str, target_lang: str) -> TranslationRoutePlan:
    return plan_translation_requirements(app_dir, source_lang, target_lang)
