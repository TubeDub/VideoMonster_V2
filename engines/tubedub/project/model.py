"""Unified .tdproj project model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

TDPROJ_FORMAT = "tubedub-project"
TDPROJ_VERSION = 1
TDPROJ_EXTENSION = ".tdproj"


@dataclass
class TdAssetRef:
    asset_id: str
    path: str
    kind: str = "media"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TdModuleState:
    module_id: str
    enabled: bool = True
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TdPipelineState:
    trace_id: str = ""
    last_run_ms: int = 0
    stages: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TdProject:
    """Single project file format for all TubeDub modules."""

    project_id: str
    title: str
    format: str = TDPROJ_FORMAT
    version: int = TDPROJ_VERSION
    created_ms: int = 0
    updated_ms: int = 0
    src_lang: str = "en"
    tgt_lang: str = "uk"
    modules: dict[str, TdModuleState] = field(default_factory=dict)
    pipeline: TdPipelineState = field(default_factory=TdPipelineState)
    assets: list[TdAssetRef] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "project_id": self.project_id,
            "title": self.title,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "modules": {k: v.to_dict() for k, v in self.modules.items()},
            "pipeline": self.pipeline.to_dict(),
            "assets": [a.to_dict() for a in self.assets],
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TdProject:
        modules = {}
        for mid, row in (data.get("modules") or {}).items():
            if isinstance(row, dict):
                modules[str(mid)] = TdModuleState(
                    module_id=str(mid),
                    enabled=bool(row.get("enabled", True)),
                    data=dict(row.get("data") or {}),
                )
        pipe_raw = data.get("pipeline") or {}
        pipeline = TdPipelineState(
            trace_id=str(pipe_raw.get("trace_id") or ""),
            last_run_ms=int(pipe_raw.get("last_run_ms") or 0),
            stages=list(pipe_raw.get("stages") or []),
            segments=list(pipe_raw.get("segments") or []),
        )
        assets = [
            TdAssetRef(
                asset_id=str(a.get("asset_id") or ""),
                path=str(a.get("path") or ""),
                kind=str(a.get("kind") or "media"),
                meta=dict(a.get("meta") or {}),
            )
            for a in (data.get("assets") or [])
            if isinstance(a, dict)
        ]
        return cls(
            project_id=str(data.get("project_id") or ""),
            title=str(data.get("title") or "Untitled"),
            format=str(data.get("format") or TDPROJ_FORMAT),
            version=int(data.get("version") or TDPROJ_VERSION),
            created_ms=int(data.get("created_ms") or 0),
            updated_ms=int(data.get("updated_ms") or 0),
            src_lang=str(data.get("src_lang") or "en"),
            tgt_lang=str(data.get("tgt_lang") or "uk"),
            modules=modules,
            pipeline=pipeline,
            assets=assets,
            meta=dict(data.get("meta") or {}),
        )
