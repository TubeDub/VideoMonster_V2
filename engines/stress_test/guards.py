"""Access control — Developer / Owner only."""

from __future__ import annotations

from typing import Any

from engines.mt.translate_guard import is_dev_mode
from engines.owner_first_run import is_owner_host


def allow_stress_test(*, ui_dev: bool = False) -> bool:
    """
    Stress Test is available when:
    - VM_DEV_MODE=1 (server developer), or
    - owner host machine, or
    - UI is in Developer mode (local desktop app).
    """
    if is_dev_mode():
        return True
    if is_owner_host():
        return True
    if ui_dev:
        return True
    return False


def allow_stress_test_request(request: Any | None = None) -> bool:
    ui_dev = False
    if request is not None:
        hdr = (request.headers.get("X-VM-Ui-Mode") or "").strip().lower()
        ui_dev = hdr == "dev"
    return allow_stress_test(ui_dev=ui_dev)
