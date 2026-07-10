"""Platform-wide trace logging (TZ Etap 8)."""

from engines.platform_diagnostics.sink import PlatformTraceSink
from engines.platform_diagnostics.trace import PlatformTraceRecord, trace_stage

__all__ = ["PlatformTraceSink", "PlatformTraceRecord", "trace_stage"]
