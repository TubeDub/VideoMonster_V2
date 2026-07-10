"""Runtime lock — downloads only inside explicit prepare / owner update sessions."""

from __future__ import annotations

_DOWNLOADS_PERMITTED = False
_OFFLINE_ONLY = False


class OfflineOnlyError(RuntimeError):
    """Raised when code attempts download/install while dub pipeline is running."""


class ModelNotPreparedError(RuntimeError):
    """Raised when a model is missing and lazy mode forbids implicit download."""

    def __init__(self, message: str = "", *, component: str = "", pair: str = ""):
        self.component = component
        self.pair = pair
        super().__init__(message or "Языковой пакет не установлен")


def set_downloads_permitted(enabled: bool) -> None:
    global _DOWNLOADS_PERMITTED
    _DOWNLOADS_PERMITTED = bool(enabled)


def downloads_permitted() -> bool:
    return _DOWNLOADS_PERMITTED


def set_offline_only(enabled: bool) -> None:
    global _OFFLINE_ONLY
    _OFFLINE_ONLY = bool(enabled)


def is_offline_only() -> bool:
    return _OFFLINE_ONLY


def assert_downloads_allowed(action: str = "download") -> None:
    if _OFFLINE_ONLY:
        raise OfflineOnlyError(
            f"Загрузка моделей запрещена во время дубляжа ({action}). "
            "Завершите «Подготовку компонентов» до старта."
        )
    if not _DOWNLOADS_PERMITTED:
        raise ModelNotPreparedError(
            f"Для этой операции нужен языковой пакет ({action}). "
            "Подтвердите загрузку в диалоге подготовки.",
            component=action,
        )


class prepare_download_session:
    """Context manager — the only normal path that permits model downloads."""

    def __enter__(self):
        set_downloads_permitted(True)
        return self

    def __exit__(self, exc_type, exc, tb):
        set_downloads_permitted(False)
        return False
