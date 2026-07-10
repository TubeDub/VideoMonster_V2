"""TubeDub Pipeline Platform — modular stage registry."""

from __future__ import annotations

from engines.pipeline_platform.contract import StageId, StageModule, StageStatus

_REGISTRY: dict[StageId, StageModule] = {}


def register_stage(module: StageModule) -> None:
    _REGISTRY[module.stage_id] = module


def get_stage(stage_id: StageId) -> StageModule | None:
    return _REGISTRY.get(stage_id)


def list_stages() -> list[dict]:
    out = []
    for sid in StageId:
        mod = _REGISTRY.get(sid)
        if mod:
            out.append(
                {
                    "stage_id": sid.value,
                    "label": mod.label(),
                    "status": mod.status().value,
                }
            )
        else:
            out.append({"stage_id": sid.value, "label": sid.value, "status": StageStatus.STUB.value})
    return out


def bootstrap_stages() -> None:
    if _REGISTRY:
        return
    from engines.pipeline_platform.stages.adapters import (
        AudioBuilderStage,
        EnterpriseTranslationStage,
        FinalMuxStage,
        NaturalTranslationStage,
        SttStage,
        TimingOptimizerStage,
        TranslationManagerStage,
        TranslationOptimizerStage,
        TtsStage,
    )

    for cls in (
        SttStage,
        TranslationManagerStage,
        EnterpriseTranslationStage,
        NaturalTranslationStage,
        TranslationOptimizerStage,
        TimingOptimizerStage,
        TtsStage,
        AudioBuilderStage,
        FinalMuxStage,
    ):
        register_stage(cls())
