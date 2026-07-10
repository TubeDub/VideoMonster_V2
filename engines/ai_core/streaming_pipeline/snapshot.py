"""AI Core 4.2 — immutable segment snapshot for streaming pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import copy


@dataclass(frozen=True)
class SegmentSnapshot:
    """Read-only segment view passed between conveyor stages."""

    segment_index: int
    _data: Mapping[str, Any]

    @classmethod
    def from_segment(cls, seg: dict[str, Any], segment_index: int) -> SegmentSnapshot:
        return cls(segment_index=segment_index, _data=MappingProxyType(copy.deepcopy(seg)))

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))

    @property
    def data(self) -> Mapping[str, Any]:
        return self._data
