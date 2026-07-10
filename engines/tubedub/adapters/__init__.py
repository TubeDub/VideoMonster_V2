"""Platform module adapters."""

from engines.tubedub.adapters.base import (
    ADAPTER_MAP,
    ArchitectureModule,
    create_adapter,
)

__all__ = ["ADAPTER_MAP", "ArchitectureModule", "create_adapter"]
