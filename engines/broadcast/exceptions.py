"""Broadcast pipeline exceptions."""


class DataCorruptionException(Exception):
    """Token integrity violated — sets before/after mismatch."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "",
        missing: set | None = None,
        extra: set | None = None,
        engine: str = "",
    ):
        super().__init__(message)
        self.stage = stage
        self.missing = missing or set()
        self.extra = extra or set()
        self.engine = engine


class SegmentFailedException(Exception):
    """Segment cannot be recovered — atomic failure."""

    def __init__(self, message: str, *, segment_index: int = -1, stage: str = ""):
        super().__init__(message)
        self.segment_index = segment_index
        self.stage = stage
